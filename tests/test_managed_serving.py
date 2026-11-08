"""Local serving against GraphCompiler, resource adapters and strict FileJournal."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
from typing import Any
from uuid import UUID

import pytest

from contexture.application.managed_generation import ReadyGeneration, prepare_generation
from contexture.application.managed_releases import (
    PROJECTION_NAME, ReleaseIntentCoordinator, VerifiedReleaseManifest, release_projection,
)
from contexture.application.managed_serving import CapturedRelease, LocalServingCoordinator
from contexture.core.graph.bindings import AppDependencyContract
from contexture.core.graph.compiler import GraphCompiler, StructuralPlan
from contexture.core.graph.errors import GraphError
from contexture.server.managed_journal import CommitUnknown, FileJournal
from tests.test_managed_generation import Probe, adapters, compiled
from tests.test_managed_owner import LATER, SCHEMA, Host, owner, seed


def uid(n: int) -> str:
    return str(UUID(int=n))


class ServingHost(Host):
    def __init__(self) -> None:
        super().__init__()
        self.denied_operations: set[str] = set()

    def decide(self, request: Any) -> Any:
        decision = super().decide(request)
        if request.operation in self.denied_operations:
            return replace(decision, allowed=False)
        return decision


class Provider:
    def __init__(self, host: ServingHost) -> None:
        self.host = host
        self.template, self.handlers = compiled()
        self.build = "build1"
        self.probes: dict[str, Probe] = {}
        self.plans: list[StructuralPlan] = []
        self.changes: dict[str, str] = {}
        self.prepare_calls = 0

    def compile(self, snapshot: Any) -> StructuralPlan:
        # Reconstruct a real exact registry with the original registrations.
        from contexture.core.graph.bindings import RegistryGeneration
        registry = RegistryGeneration(build="pinned", registrations=[
            self.template.bindings[n].registration for n in self.template.index.preorder])
        plan = GraphCompiler.compile(snapshot.definition, registry=registry,
            compiler_contract_version="compiler-v1", compiler_build=self.build,
            app_dependency_contract=AppDependencyContract("deps1", "build1"),
            environment_compatibility=("python",), locale="en")
        self.plans.append(plan)
        return plan

    def verify(self, request: Any, snapshot: Any, context: Any) -> VerifiedReleaseManifest:
        assert self.host.fences > 0 and context == self.host.context
        plan = self.compile(snapshot)
        return VerifiedReleaseManifest(snapshot.definition.graph_id, snapshot.revision,
                                       snapshot.definition_digest, plan.manifest)

    async def prepare(self, captured: CapturedRelease) -> ReadyGeneration:
        assert self.host.fences == 0
        self.prepare_calls += 1
        plan = self.compile(captured.snapshot)
        probe = self.probes.setdefault(captured.attempt_id, Probe())
        return await prepare_generation(plan, graph_id=self.changes.get("graph_id", "g"),
            revision=self.changes.get("revision", captured.request.revision),
            generation_uuid=self.changes.get("generation_id", captured.generation_id),
            adapters=adapters(plan, probe))


@pytest.fixture
def system():
    # Native Linux temp, not Windows 9p: exercise strict journal durability.
    with tempfile.TemporaryDirectory(prefix="contexture-serving-") as temp:
        path = Path(temp) / "store"
        with FileJournal.open(path, schema=SCHEMA) as journal:
            host = ServingHost()
            manager = owner(journal, host, projections={PROJECTION_NAME: release_projection()})
            provider = Provider(host)
            value = seed()
            value["seed"]["inline"] = provider.template.definition.as_json()
            for node in value["seed"]["inline"]["nodes"]:
                node["mutable"] = True
            manager.commit_bootstrap(manager.preview_bootstrap(value, expires_at=LATER))
            intents = ReleaseIntentCoordinator(manager, manifests=provider)
            yield path, journal, host, manager, provider, intents
            manager.close()


def accept(system: Any, epoch: int = 0, *, revision: str | None = None) -> None:
    _, _, _, manager, provider, intents = system
    snapshot = manager.read("g", revision)
    plan = provider.compile(snapshot)
    intents.request({"schema": "contexture.release-request/v1", "graph_id": "g",
        "target_id": "target", "revision": snapshot.revision,
        "definition_digest": snapshot.definition_digest, "manifest_digest": plan.manifest.manifest_digest,
        "expected_desired_epoch": str(epoch), "idempotency_key": f"release-{epoch}",
        "policy": "exact", "reason": "local release", "approval_ref": "approved"})


def serving(system: Any, **kwargs: Any) -> LocalServingCoordinator:
    return LocalServingCoordinator(system[3], graph_id="g", target_id="target",
        instance_id="replica", boot_id=uid(1000), provider=system[4], **kwargs)


async def attempt(coordinator: LocalServingCoordinator, n: int):
    return await coordinator.prepare(attempt_id=uid(n), generation_id=uid(100 + n))


def events(system: Any, tag: str):
    return [event for record in system[1].records() for event in record["events"] if event["type"] == tag]


@pytest.mark.asyncio
async def test_real_swap_lease_scope_exact_proof_and_unique_attempt(system):
    accept(system)
    coordinator = serving(system)
    result = await attempt(coordinator, 1)
    assert result.status == "local_observed" and result.captured.request.policy == "exact"
    async with coordinator.request_lease() as lease:
        assert lease.release == result.captured
        assert lease.generation.context.generation_uuid == uid(101)
        assert tuple(lease.handles) == ("z", "a", "m")
    with pytest.raises(GraphError) as error:
        await coordinator.prepare(attempt_id=uid(1), generation_id=uid(102))
    assert error.value.code == "identity_conflict"
    assert len(events(system, "ReleasePreparationSucceeded")) == 1
    assert len(events(system, "ReplicaObserved")) == 1
    for call in system[2].calls:
        if call.operation.startswith("release."):
            assert call.impact == ("a", "m", "z")
            assert call.approval_subject_digest == result.captured.request.digest()
            assert call.candidate_digest == result.captured.request.manifest_digest
    assert system[4].handlers == dict.fromkeys(system[4].handlers, 0)
    await coordinator.shutdown()
    assert coordinator.counts == {"preparing": 0, "retiring": 0, "cleanup_errors": 0, "active": 0}


@pytest.mark.asyncio
async def test_slow_epoch_one_never_overwrites_fast_epoch_two(system):
    accept(system)
    coordinator = serving(system)
    slow = Probe()
    slow.block_enter = "m"
    system[4].probes[uid(1)] = slow
    first = asyncio.create_task(attempt(coordinator, 1))
    await slow.entered.wait()
    accept(system, 1)
    fast = await attempt(coordinator, 2)
    assert fast.status == "local_observed"
    slow.proceed.set()
    assert (await first).status == "superseded"
    async with coordinator.request_lease() as lease:
        assert lease.release.epoch == "2" and lease.generation.context.generation_uuid == uid(102)
    await coordinator.shutdown()
    assert slow.events[-3:] == ["close:m", "close:a", "close:z"]
    assert len(events(system, "ReleasePreparationSucceeded")) == 2
    assert len(events(system, "ReplicaObserved")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["enter", "revision", "generation_id", "registry", "permission"])
async def test_candidate_failure_keeps_old_active_and_never_runs_handlers(system, failure):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    accept(system, 1)
    probe = Probe()
    system[4].probes[uid(2)] = probe
    if failure == "enter":
        probe.fail_enter = "m"
    elif failure in ("revision", "generation_id"):
        system[4].changes[failure] = "2" if failure == "revision" else uid(999)
    else:
        probe.block_enter = "m"
    task = asyncio.create_task(attempt(coordinator, 2))
    if failure in ("registry", "permission"):
        await probe.entered.wait()
        if failure == "registry":
            system[4].build = "changed-current-code"
        else:
            system[2].denied_operations.add("release.swap")
        probe.proceed.set()
    assert (await task).status == "failed"
    async with coordinator.request_lease() as lease:
        assert lease.generation.context.generation_uuid == uid(101)
    assert len(events(system, "ReplicaObserved")) == 1
    assert system[4].handlers == dict.fromkeys(system[4].handlers, 0)
    await coordinator.shutdown()
    assert not coordinator.cleanup_errors


@pytest.mark.asyncio
async def test_old_lease_delays_close_without_blocking_new_requests(system):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    async with coordinator.request_lease() as old:
        # The pointer is captured before a hypothetical external auth await.
        await asyncio.sleep(0)
        accept(system, 1)
        await attempt(coordinator, 2)
        await asyncio.sleep(0)
        assert old.generation.resource_state == "draining" and len(old.handles) == 3
        async with coordinator.request_lease() as new:
            assert new.release.epoch == "2"
        assert not any(e.startswith("close:") for e in system[4].probes[uid(1)].events)
    await coordinator.shutdown()
    assert old.generation.resource_state == "retired"


@pytest.mark.asyncio
async def test_same_revision_different_manifest_swaps_distinct_generations(system):
    accept(system)
    coordinator = serving(system)
    first = await attempt(coordinator, 1)
    system[4].build = "build2"
    accept(system, 1)
    second = await attempt(coordinator, 2)
    assert first.captured.request.revision == second.captured.request.revision == "1"
    assert first.captured.request.manifest_digest != second.captured.request.manifest_digest
    async with coordinator.request_lease() as lease:
        assert lease.generation.context.generation_uuid == uid(102)
    await coordinator.shutdown()


@pytest.mark.asyncio
async def test_new_boot_does_not_adopt_observations(system):
    accept(system)
    first = serving(system)
    await attempt(first, 1)
    await first.shutdown()
    fresh = LocalServingCoordinator(system[3], graph_id="g", target_id="target",
        instance_id="replica", boot_id=uid(1001), provider=system[4])
    assert events(system, "ReplicaObserved")
    with pytest.raises(GraphError, match="No local serving"):
        fresh.request_lease()
    await fresh.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["record.before_rename", "record.renamed"])
async def test_success_append_unknown_never_swaps(system, stage):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    accept(system, 1)
    def fault(step):
        if step == stage:
            raise OSError("uncertain success append")
    system[1]._fault = fault
    result = await attempt(coordinator, 2)
    assert result.status == "failed" and system[3].writes_stopped
    assert isinstance(result.error, (OSError, CommitUnknown))
    async with coordinator.request_lease() as lease:
        assert lease.generation.context.generation_uuid == uid(101)
    system[1]._fault = None
    await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("published", [False, True])
async def test_observation_failure_keeps_real_new_pointer_and_recovers_without_duplicate(system, published):
    path, journal, host, manager, provider, _ = system
    accept(system)
    coordinator = serving(system)
    # First append is success, second observation: inject only the latter.
    publications = 0
    def fault(step):
        nonlocal publications
        if step == "record.before_rename":
            publications += 1
            if publications == 2 and not published:
                raise OSError("observation not published")
        if step == "record.renamed" and publications == 2 and published:
            raise OSError("observation uncertain")
    journal._fault = fault
    result = await attempt(coordinator, 1)
    assert result.status == "serving_unobserved" and manager.writes_stopped
    async with coordinator.request_lease() as lease:
        assert lease.release.epoch == "1"
        assert lease.generation.context.generation_uuid == uid(101)
    assert coordinator.retry_observation().status == "serving_unobserved"
    with pytest.raises(GraphError, match="capacity"):
        await attempt(coordinator, 2)
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host, projections={PROJECTION_NAME: release_projection()})
        assert coordinator.retry_observation(recovered_owner=recovered).status == "local_observed"
        assert coordinator.retry_observation().status == "local_observed"
        observations = [e for r in reopened.records() for e in r["events"] if e["type"] == "ReplicaObserved"]
        assert len(observations) == 1
        assert provider.handlers == dict.fromkeys(provider.handlers, 0)
        await coordinator.shutdown()
        recovered.close()


@pytest.mark.asyncio
async def test_bounded_parallel_preparation_refuses_without_queue_and_cancel_unwinds(system):
    accept(system)
    coordinator = serving(system, max_preparations=2)
    probes = [Probe(), Probe()]
    tasks = []
    for n, probe in enumerate(probes, 1):
        probe.block_enter = "m"
        system[4].probes[uid(n)] = probe
        tasks.append(asyncio.create_task(attempt(coordinator, n)))
        await probe.entered.wait()
    with pytest.raises(GraphError) as error:
        await attempt(coordinator, 3)
    assert error.value.code == "resource_busy" and system[4].prepare_calls == 2
    assert coordinator.counts["preparing"] == 2
    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task
    assert all(p.partial_released and p.events[-2:] == ["close:a", "close:z"] for p in probes)
    assert len(events(system, "ReleasePreparationFailed")) == 2
    await coordinator.shutdown()
    assert coordinator.counts["preparing"] == coordinator.counts["retiring"] == 0


@pytest.mark.asyncio
async def test_shutdown_waits_for_real_lease_even_if_cancelled_and_reports_cleanup_errors(system):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    system[4].probes[uid(1)].fail_close = {"m"}
    async with coordinator.request_lease():
        shutdown = asyncio.create_task(coordinator.shutdown())
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
        with pytest.raises(GraphError):
            coordinator.request_lease()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    await coordinator.shutdown()
    assert coordinator.counts == {"preparing": 0, "retiring": 0, "cleanup_errors": 1, "active": 0}
    assert isinstance(coordinator.cleanup_errors[0], BaseExceptionGroup)


@pytest.mark.asyncio
async def test_retirement_backpressure_never_blocks_current_request_admission(system):
    accept(system)
    coordinator = serving(system, max_retirements=1, max_preparations=1)
    await attempt(coordinator, 1)
    async with coordinator.request_lease():
        accept(system, 1)
        await attempt(coordinator, 2)
        await asyncio.sleep(0)
        assert coordinator.counts["retiring"] == 1
        with pytest.raises(GraphError, match="capacity"):
            await attempt(coordinator, 3)
        async with coordinator.request_lease() as current:
            assert current.release.epoch == "2"
    await coordinator.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cooperatively_cancels_inflight_preparation(system):
    accept(system)
    coordinator = serving(system)
    probe = Probe()
    probe.block_enter = "m"
    system[4].probes[uid(1)] = probe
    prepare = asyncio.create_task(attempt(coordinator, 1))
    await probe.entered.wait()
    await coordinator.shutdown()
    assert prepare.cancelled() and probe.partial_released
    assert coordinator.counts["preparing"] == coordinator.counts["retiring"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, -1, True])
async def test_invalid_limits_refuse(system, limit):
    with pytest.raises(ValueError):
        serving(system, max_preparations=limit)


@pytest.mark.asyncio
async def test_unknown_during_prepare_blocks_ready_and_preserves_old(system):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    accept(system, 1)
    probe = Probe()
    probe.block_enter = "m"
    system[4].probes[uid(2)] = probe
    task = asyncio.create_task(attempt(coordinator, 2))
    await probe.entered.wait()
    def fault(step):
        if step == "record.renamed":
            raise OSError("other owner write uncertain")
    system[1]._fault = fault
    with pytest.raises((OSError, CommitUnknown)):
        accept(system, 2)
    probe.proceed.set()
    result = await task
    assert result.status == "failed" and isinstance(result.error, GraphError)
    assert result.error.code == "commit_unknown"
    async with coordinator.request_lease() as lease:
        assert lease.release.epoch == "1"
    system[1]._fault = None
    await coordinator.shutdown()
    assert probe.events[-3:] == ["close:m", "close:a", "close:z"]


@pytest.mark.asyncio
async def test_historical_rollback_uses_full_pinned_snapshot_not_head(system):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    manager = system[3]
    transaction = {"schema": "contexture.transaction/v1", "graph_id": "g",
        "base_revision": "1", "idempotency_key": "rename", "reason": "advance head",
        "correlation_id": "trace", "commands": [{"op": "rename_node", "node_id": "z",
                                                  "new_name": "new-name"}]}
    manager.commit(manager.preview(transaction, expires_at=LATER))
    accept(system, 1)
    await attempt(coordinator, 2)
    accept(system, 2, revision="1")
    rollback = await attempt(coordinator, 3)
    assert rollback.status == "local_observed" and manager.read("g").revision == "2"
    async with coordinator.request_lease() as lease:
        assert lease.release.epoch == "3" and lease.generation.context.revision == "1"
        assert lease.generation.plan.definition.nodes["z"].name == "z"
    await coordinator.shutdown()


@pytest.mark.asyncio
async def test_current_permission_denied_before_any_resource_prepare(system):
    accept(system)
    coordinator = serving(system)
    system[2].denied_operations.add("release.prepare")
    with pytest.raises(GraphError) as error:
        await attempt(coordinator, 1)
    assert error.value.code == "permission_denied"
    assert system[4].prepare_calls == 0
    assert system[4].handlers == dict.fromkeys(system[4].handlers, 0)
    assert not events(system, "ReleasePreparationSucceeded")
    await coordinator.shutdown()


@pytest.mark.asyncio
async def test_retry_rejects_reseeded_independent_store_with_identical_release_history(system):
    path, journal, host, manager, _, _ = system
    accept(system)
    coordinator = serving(system)
    host.denied_operations.add("release.observe")
    result = await attempt(coordinator, 1)
    assert result.status == "serving_unobserved"
    source = journal.records()
    # Replay every exact graph/request/ready fact into an independently created
    # durable journal; semantic history is identical, durable origin is not.
    with FileJournal.open(path.parent / "independent", schema=SCHEMA) as other:
        for record in source:
            other.append(record["events"], actor_ref=record["actor_ref"],
                command_receipt=record.get("command_receipt"),
                correlation_id=record.get("correlation_id"),
                event_batch_id=record["event_batch_id"], written_at=record["written_at"])
        wrong = owner(other, host, projections={PROJECTION_NAME: release_projection()})
        assert other.records()[0]["store_id"] != source[0]["store_id"]
        assert wrong.read("g") == manager.read("g")
        assert wrong.projection(PROJECTION_NAME, graph_id="g") == manager.projection(PROJECTION_NAME, graph_id="g")
        host.denied_operations.clear()
        host.calls.clear()
        before = other.records()
        manager.close()
        journal.close()
        refused = coordinator.retry_observation(recovered_owner=wrong)
        assert refused.status == "serving_unobserved"
        assert isinstance(refused.error, GraphError) and refused.error.code == "contract_mismatch"
        assert coordinator.owner is manager
        assert other.records() == before and not host.calls
        # A failed candidate's authorization must not replace the closed owner.
        with FileJournal.open(path, schema=SCHEMA) as reopened:
            recovered = owner(reopened, host, projections={PROJECTION_NAME: release_projection()})
            host.denied_operations.add("release.observe")
            denied = coordinator.retry_observation(recovered_owner=recovered)
            assert denied.status == "serving_unobserved" and coordinator.owner is manager
            host.denied_operations.clear()
            assert coordinator.retry_observation(recovered_owner=recovered).status == "local_observed"
            assert coordinator.owner is recovered
            assert len([e for r in reopened.records() for e in r["events"] if e["type"] == "ReplicaObserved"]) == 1
            await coordinator.shutdown()
            recovered.close()
        wrong.close()


@pytest.mark.asyncio
async def test_already_observed_retry_also_refuses_independent_store(system):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    path, _, host, manager, _, _ = system
    with FileJournal.open(path.parent / "other", schema=SCHEMA) as journal:
        wrong = owner(journal, host, projections={PROJECTION_NAME: release_projection()})
        wrong.commit_bootstrap(wrong.preview_bootstrap(seed(), expires_at=LATER))
        before = journal.watermark
        with pytest.raises(GraphError) as error:
            coordinator.retry_observation(recovered_owner=wrong)
        assert error.value.code == "contract_mismatch"
        assert coordinator.owner is manager and journal.watermark == before
        wrong.close()
    await coordinator.shutdown()


@pytest.mark.asyncio
async def test_manager_created_before_swap_captures_generation_only_when_entered(system):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    delayed = coordinator.request_lease()
    accept(system, 1)
    await attempt(coordinator, 2)
    await asyncio.sleep(0)
    async with delayed as lease:
        assert lease.release.epoch == "2"
        assert lease.generation.context.generation_uuid == uid(102)
    await coordinator.shutdown()


@pytest.mark.asyncio
async def test_entered_lease_stays_pinned_across_external_authorization_await(system):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    entered, authorize = asyncio.Event(), asyncio.Event()
    host = system[2]
    host.calls.clear()

    async def request():
        async with coordinator.request_lease() as lease:
            entered.set()
            await authorize.wait()  # External current authorization can suspend.
            assert lease.release.epoch == "1"
            assert lease.generation.context.generation_uuid == uid(101)
            assert lease.generation.resource_state == "draining" and len(lease.handles) == 3
            # A resource lease is not a permission grant; the Host can deny.
            with pytest.raises(GraphError) as error:
                system[3].read("g")
            assert error.value.code == "permission_denied"

    task = asyncio.create_task(request())
    await entered.wait()
    assert not host.calls  # Lease entry has no policy callbacks or authorization.
    accept(system, 1)
    await attempt(coordinator, 2)
    await asyncio.sleep(0)
    assert not any(e.startswith("close:") for e in system[4].probes[uid(1)].events)
    host.denied = True
    authorize.set()
    await task
    host.denied = False
    async with coordinator.request_lease() as current:
        assert current.release.epoch == "2"
    await coordinator.shutdown()


@pytest.mark.asyncio
async def test_delayed_lease_entry_rechecks_shutdown(system):
    accept(system)
    coordinator = serving(system)
    await attempt(coordinator, 1)
    delayed = coordinator.request_lease()
    await coordinator.shutdown()
    with pytest.raises(GraphError) as error:
        async with delayed:
            pytest.fail("Closed coordinator admitted a delayed lease")
    assert error.value.code == "not_ready"