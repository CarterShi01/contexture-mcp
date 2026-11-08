"""WP07 resource-only preparation against real pinned GraphCompiler plans."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from contexture.application.managed_generation import (
    PreparationAdapterRegistration, PreparationContext, ReadyGeneration,
    prepare_generation,
)
from contexture.core.graph.bindings import (
    AppDependencyContract, BindingRegistration, RegistryGeneration, ResolvedBinding,
)
from contexture.core.graph.compiler import GraphCompiler, StructuralPlan
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import GraphDefinition, Portability

GENERATION = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
PORTABLE = {"kind": "portable", "contract_id": "resource-test-v1"}


def compiled(*, disclosure: bool = False) -> tuple[StructuralPlan, dict[str, int]]:
    calls: dict[str, int] = dict.fromkeys(("handler", "decoder", "validator", "factory"), 0)

    def forbidden(name: str) -> Any:
        def call(*args: object, **kwargs: object) -> object:
            calls[name] += 1
            raise AssertionError("Forbidden execution: " + name)
        return call

    registrations = [BindingRegistration(
        binding_id=n, version="1", kind="registered",
        portability=Portability.from_json(PORTABLE), effect="read_only", abi="opaque-call-v1",
        artifact_digest="sha256:" + "a" * 64, input_schema=True,
        validator_contract_id="v1", decoder_contract_id="d1",
        default_contract_id="defaults1", coercion_contract_id="coercion1",
        **{name: forbidden(name) for name in calls}) for n in ("a", "z", "m")]
    registry = RegistryGeneration(build="pinned", registrations=registrations)
    nodes: list[dict[str, Any]] = []
    # Forest order z, a, m differs from both node-array and lexical order.
    for n in ("m", "a", "z"):
        node: dict[str, Any] = dict(node_id=n, kind="tool", name=n, description="Resource")
        if not disclosure:
            node["binding"] = dict(kind="registered", binding_id=n, version="1", portability=PORTABLE)
        nodes.append(node)
    graph = GraphDefinition.from_json(dict(
        schema="contexture.graph/v1", semantic_version="1", graph_id="g", application_name="test",
        mode="disclosure_only" if disclosure else "runtime", nodes=nodes,
        roots=[dict(node_id=n, plane="model") for n in ("z", "a", "m")],
        presentations={"default_locale": "en", "catalogs": []}))
    plan = GraphCompiler.compile(
        graph, registry=registry, compiler_contract_version="compiler-v1", compiler_build="build1",
        app_dependency_contract=AppDependencyContract("deps1", "build1"),
        environment_compatibility=("python",), locale="en",
        no_execution_registry_digest="sha256:" + "e" * 64 if disclosure else None)
    assert calls == dict.fromkeys(calls, 0)  # compilation remained pure
    return plan, calls


class FatalCleanup(BaseException):
    pass


class Probe:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.contexts: list[PreparationContext] = []
        self.bindings: list[ResolvedBinding] = []
        self.fail_enter: str | None = None
        self.fail_close: set[str] = set()
        self.block_enter: str | None = None
        self.block_close: str | None = None
        self.entered = asyncio.Event()
        self.closing = asyncio.Event()
        self.proceed = asyncio.Event()
        self.partial_released = False

    def prepare(self, *, binding: ResolvedBinding, node_id: str,
                context: PreparationContext) -> AbstractAsyncContextManager[object]:
        self.events.append("prepare:" + node_id)
        self.contexts.append(context)
        self.bindings.append(binding)
        return self.resource(node_id)

    @asynccontextmanager
    async def resource(self, node_id: str) -> AsyncIterator[object]:
        self.events.append("enter:" + node_id)
        if node_id == self.fail_enter:
            raise RuntimeError("enter failed")
        if node_id == self.block_enter:
            self.entered.set()
            try:
                await self.proceed.wait()
            except BaseException:
                # The adapter owns rollback before __aenter__ succeeds.
                self.partial_released = True
                self.events.append("partial:" + node_id)
                raise
        try:
            yield object()
        finally:
            self.events.append("close:" + node_id)
            if node_id == self.block_close:
                self.closing.set()
                await self.proceed.wait()
            if node_id in self.fail_close:
                raise FatalCleanup("exit failed: " + node_id)


def adapters(plan: StructuralPlan, probe: Probe) -> dict[str, PreparationAdapterRegistration]:
    return {n: PreparationAdapterRegistration(
        binding_id=b.entry.binding.binding_id, version=b.entry.binding.version,
        abi=b.entry.abi, contract_digest=b.entry.contract_digest, adapter=probe)
        for n, b in plan.bindings.items()}


async def prepare(plan: StructuralPlan, probe: Probe, generation: str = GENERATION) -> ReadyGeneration:
    return await prepare_generation(plan, graph_id="g", revision="7", generation_uuid=generation,
                                    adapters=adapters(plan, probe))


@pytest.mark.asyncio
async def test_two_generations_own_separate_resources_and_no_execution() -> None:
    plan, calls = compiled()
    one, two = Probe(), Probe()
    first = await prepare(plan, one)
    second = await prepare(plan, two, OTHER)
    expected = [s + n for n in ("z", "a", "m") for s in ("prepare:", "enter:")]
    assert one.events == two.events == expected
    assert all(b is plan.bindings[n] for n, b in zip(("z", "a", "m"), one.bindings))
    assert one.contexts == [PreparationContext("g", "7", GENERATION)] * 3
    assert first.plan is plan and second.plan is plan
    async with first.lease() as left, second.lease() as right:
        assert tuple(left) == tuple(right) == ("z", "a", "m")
        assert all(left[n] is not right[n] for n in left)
    await first.retire()
    assert one.events[-3:] == ["close:m", "close:a", "close:z"]
    assert not any(e.startswith("close:") for e in two.events)
    async with second.lease() as right:
        assert len(right) == 3
    await second.retire()
    assert calls == dict.fromkeys(calls, 0)
    assert first.resource_state == second.resource_state == "retired"


@pytest.mark.asyncio
async def test_failed_candidate_unwinds_only_acquired_reverse_not_other_generation() -> None:
    plan, calls = compiled()
    live, failing = Probe(), Probe()
    old = await prepare(plan, live)
    failing.fail_enter = "m"
    with pytest.raises(RuntimeError, match="enter failed"):
        await prepare(plan, failing, OTHER)
    assert failing.events == ["prepare:z", "enter:z", "prepare:a", "enter:a",
                              "prepare:m", "enter:m", "close:a", "close:z"]
    assert not any(e.startswith("close:") for e in live.events)
    async with old.lease() as handles:
        assert len(handles) == 3
    await old.retire()
    assert calls == dict.fromkeys(calls, 0)


@pytest.mark.asyncio
async def test_cancelled_enter_and_repeated_cancellation_wait_for_exact_cleanup() -> None:
    plan, _ = compiled()
    probe = Probe()
    probe.block_enter, probe.block_close = "m", "a"
    task = asyncio.create_task(prepare(plan, probe))
    await probe.entered.wait()
    task.cancel()
    await probe.closing.wait()
    assert probe.partial_released
    task.cancel()  # a second cancellation cannot interrupt owned stack disposal
    await asyncio.sleep(0)
    assert not task.done()
    assert "close:z" not in probe.events
    probe.proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert probe.events[-3:] == ["partial:m", "close:a", "close:z"]


@pytest.mark.asyncio
async def test_single_cancellation_preserves_cancelled_task_semantics() -> None:
    plan, _ = compiled()
    probe = Probe()
    probe.block_enter = "a"
    task = asyncio.create_task(prepare(plan, probe))
    await probe.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled() and probe.partial_released
    assert probe.events[-2:] == ["partial:a", "close:z"]


@pytest.mark.asyncio
async def test_leases_drain_no_early_close_no_new_lease_and_idempotent_retirement() -> None:
    plan, _ = compiled()
    probe = Probe()
    ready = await prepare(plan, probe)
    async with ready.lease(), ready.lease():
        first = asyncio.create_task(ready.retire())
        await asyncio.sleep(0)
        assert ready.resource_state == "draining"
        with pytest.raises(GraphError, match="draining"):
            async with ready.lease():
                pytest.fail("New lease admitted while draining")
        assert not any(e.startswith("close:") for e in probe.events)
        second = asyncio.create_task(ready.retire())
        await asyncio.sleep(0)
        assert not first.done() and not second.done()
    await asyncio.gather(first, second)
    await ready.retire()
    assert probe.events[-3:] == ["close:m", "close:a", "close:z"]
    assert sum(e.startswith("close:") for e in probe.events) == 3
    with pytest.raises(GraphError, match="retired"):
        async with ready.lease():
            pytest.fail("Lease admitted after retirement")


@pytest.mark.asyncio
async def test_cancelled_retirement_waits_for_lease_and_cleanup_before_propagating() -> None:
    plan, _ = compiled()
    probe = Probe()
    probe.block_close = "m"
    ready = await prepare(plan, probe)
    async with ready.lease():
        task = asyncio.create_task(ready.retire())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and "close:m" not in probe.events
    await probe.closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    probe.proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await ready.retire()
    assert ready.resource_state == "retired"
    assert probe.events[-3:] == ["close:m", "close:a", "close:z"]


@pytest.mark.asyncio
async def test_cleanup_baseexceptions_all_accounted_and_stack_not_abandoned() -> None:
    plan, _ = compiled()
    probe = Probe()
    probe.fail_close = {"m", "z"}
    ready = await prepare(plan, probe)
    for _ in range(2):
        with pytest.raises(BaseExceptionGroup) as raised:
            await ready.retire()
        assert [str(e) for e in raised.value.exceptions] == ["exit failed: m", "exit failed: z"]
    assert ready.resource_state == "cleanup_failed"  # never pretend a failed close succeeded
    assert probe.events[-3:] == ["close:m", "close:a", "close:z"]
    assert sum(e.startswith("close:") for e in probe.events) == 3


@pytest.mark.asyncio
async def test_prepare_failure_retains_original_and_all_cleanup_errors() -> None:
    plan, _ = compiled()
    probe = Probe()
    probe.fail_enter, probe.fail_close = "m", {"a", "z"}
    with pytest.raises(BaseExceptionGroup) as raised:
        await prepare(plan, probe)
    assert raised.value.subgroup(RuntimeError) is not None
    cleanup = raised.value.subgroup(FatalCleanup)
    assert cleanup is not None
    inner = cleanup.exceptions[0]
    assert isinstance(inner, BaseExceptionGroup)
    assert [str(e) for e in inner.exceptions] == ["exit failed: a", "exit failed: z"]
    assert probe.events[-2:] == ["close:a", "close:z"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("abi", "unknown-abi"), ("contract_digest", "sha256:" + "b" * 64),
    ("binding_id", "wrong"), ("version", "2"),
])
async def test_wrong_pinned_adapter_contract_fails_before_any_prepare(field: str, value: str) -> None:
    plan, calls = compiled()
    probe = Probe()
    registered = adapters(plan, probe)
    registered["m"] = replace(registered["m"], **{field: value})
    with pytest.raises(GraphError, match="pinned ABI/contract"):
        await prepare_generation(plan, graph_id="g", revision="7", generation_uuid=GENERATION,
                                 adapters=registered)
    assert probe.events == [] and calls == dict.fromkeys(calls, 0)


@pytest.mark.asyncio
async def test_disclosure_unbound_tools_never_request_adapter_or_factory() -> None:
    plan, calls = compiled(disclosure=True)
    assert plan.bindings == {}
    ready = await prepare_generation(plan, graph_id="g", revision="0", generation_uuid=GENERATION,
                                     adapters={})
    async with ready.lease() as handles:
        assert handles == {}
    await ready.retire()
    assert calls == dict.fromkeys(calls, 0)


@pytest.mark.asyncio
async def test_missing_adapter_never_guesses_or_calls_opaque_factory() -> None:
    plan, calls = compiled()
    with pytest.raises(GraphError, match="match resolved"):
        await prepare_generation(plan, graph_id="g", revision="7", generation_uuid=GENERATION,
                                 adapters={})
    assert calls == dict.fromkeys(calls, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"revision": "01"}, {"revision": 1}, {"revision": "-1"},
    {"revision": "1" * 21}, {"graph_id": "other"}, {"graph_id": ""},
    {"generation_uuid": GENERATION.replace("-", "")},
    {"generation_uuid": "00000000-0000-4000-A000-000000000001"},
    {"generation_uuid": "bad"},
])
async def test_explicit_canonical_identity_validation_before_effects(changes: dict[str, Any]) -> None:
    plan, _ = compiled()
    probe = Probe()
    options: dict[str, Any] = dict(graph_id="g", revision="7", generation_uuid=GENERATION,
                                   adapters=adapters(plan, probe))
    with pytest.raises(GraphError):
        await prepare_generation(plan, **(options | changes))
    assert probe.events == []


@pytest.mark.asyncio
async def test_immutable_ready_private_handles_and_lease_finally_on_baseexception() -> None:
    plan, _ = compiled()
    ready = await prepare(plan, Probe())
    with pytest.raises(FrozenInstanceError):
        setattr(ready, "plan", plan)
    with pytest.raises(FrozenInstanceError):
        setattr(ready.context, "revision", "8")
    assert not hasattr(ready, "handles") and not hasattr(ready, "as_json")
    assert not hasattr(ready, "active") and not hasattr(ready, "invoke")
    assert "_resources" not in repr(ready)
    with pytest.raises(FatalCleanup):
        async with ready.lease() as handles:
            assert isinstance(handles, Mapping)
            with pytest.raises(TypeError):
                handles["z"] = object()  # type: ignore[index]
            raise FatalCleanup("lease body")
    await ready.retire()  # proves the failed body did not strand the refcount
    with pytest.raises(TypeError, match="prepare_generation"):
        ReadyGeneration()


def test_event_loop_affinity_even_for_retired_generation() -> None:
    plan, _ = compiled()

    async def make() -> ReadyGeneration:
        ready = await prepare(plan, Probe())
        await ready.retire()
        return ready

    ready = asyncio.run(make())

    async def wrong_loop() -> None:
        with pytest.raises(GraphError, match="different event loop"):
            await ready.retire()
        with pytest.raises(GraphError, match="different event loop"):
            async with ready.lease():
                pytest.fail("Wrong-loop lease admitted")
        with pytest.raises(GraphError, match="different event loop"):
            _ = ready.resource_state

    asyncio.run(wrong_loop())


@pytest.mark.asyncio
async def test_cancelled_cleanup_reports_error_and_cancellation_without_skipping_exits() -> None:
    plan, _ = compiled()
    probe = Probe()
    probe.block_close, probe.fail_close = "m", {"m", "z"}
    ready = await prepare(plan, probe)
    task = asyncio.create_task(ready.retire())
    await probe.closing.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    probe.proceed.set()
    with pytest.raises(BaseExceptionGroup) as raised:
        await task
    assert raised.value.subgroup(asyncio.CancelledError) is not None
    assert raised.value.subgroup(FatalCleanup) is not None
    assert probe.events[-3:] == ["close:m", "close:a", "close:z"]
    assert ready.resource_state == "cleanup_failed"
    with pytest.raises(BaseExceptionGroup):
        await ready.retire()
    assert sum(e.startswith("close:") for e in probe.events) == 3


@pytest.mark.asyncio
async def test_cancelled_lease_body_releases_refcount_before_retirement() -> None:
    plan, _ = compiled()
    probe = Probe()
    ready = await prepare(plan, probe)
    held = asyncio.Event()
    wait = asyncio.Event()

    async def holder() -> None:
        async with ready.lease():
            held.set()
            await wait.wait()

    task = asyncio.create_task(holder())
    await held.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await ready.retire()
    assert ready.resource_state == "retired"
    assert probe.events[-3:] == ["close:m", "close:a", "close:z"]


@pytest.mark.asyncio
async def test_adapter_table_is_captured_before_first_acquisition() -> None:
    plan, _ = compiled()
    original, replacement = Probe(), Probe()
    original.block_enter = "z"
    table = adapters(plan, original)
    task = asyncio.create_task(prepare_generation(
        plan, graph_id="g", revision="7", generation_uuid=GENERATION, adapters=table))
    await original.entered.wait()
    table.update(adapters(plan, replacement))
    original.proceed.set()
    ready = await task
    assert replacement.events == []
    assert len(original.bindings) == 3
    await ready.retire()


@pytest.mark.asyncio
async def test_synchronous_adapter_failure_also_unwinds_acquired_resources() -> None:
    plan, _ = compiled()
    probe = Probe()

    class BrokenAdapter:
        def prepare(self, *, binding: ResolvedBinding, node_id: str,
                    context: PreparationContext) -> AbstractAsyncContextManager[object]:
            raise FatalCleanup("manager construction failed")

    table = adapters(plan, probe)
    table["m"] = replace(table["m"], adapter=BrokenAdapter())
    with pytest.raises(FatalCleanup, match="manager construction"):
        await prepare_generation(plan, graph_id="g", revision="7", generation_uuid=GENERATION,
                                 adapters=table)
    assert probe.events == ["prepare:z", "enter:z", "prepare:a", "enter:a", "close:a", "close:z"]