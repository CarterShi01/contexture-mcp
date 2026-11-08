"""Bounded runtime through real compiler, strict FileJournal and serving swaps."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import tempfile
from typing import Any, cast

import pytest

from contexture.application.managed_generation import (
    PreparationAdapterRegistration, prepare_generation,
)
from contexture.application.managed_releases import (
    PROJECTION_NAME, ReleaseIntentCoordinator, VerifiedReleaseManifest, release_projection,
)
from contexture.application.managed_runtime import (
    ExecutionAdapterRegistration, ManagedRuntime, PythonManagedCallAdapter,
)
from contexture.application.managed_serving import LocalServingCoordinator
from contexture.core.graph.bindings import (
    AppDependencyContract, BindingRegistration, Effect, RegistryGeneration, Schema,
)
from contexture.core.graph.canonical import freeze_json
from contexture.core.graph.compiler import GraphCompiler
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import GraphDefinition, Portability
from contexture.core.graph.policy import AdmissionDecision
from contexture.core.graph.references import ManagedReference
from contexture.server.managed_journal import FileJournal
from tests.test_managed_owner import LATER, SCHEMA, Host, owner, seed
from tests.test_managed_serving import uid


class Execution:
    def __init__(self):
        self.calls = []
        self.results = []
        self.resources = {}
        self.closed = []

    def registration(self, node, version="A", *, effect: Effect = "read_only", abi="python-managed-call/v1"):
        def decoder(arguments):
            self.calls.append((version, node, "decoder"))
            arguments.setdefault("count", 2)
            arguments["count"] = int(arguments["count"])
            return arguments

        def validator(decoded):
            self.calls.append((version, node, "validator"))
            if decoded["count"] < 0:
                raise GraphError("schema_invalid", "Negative native count")

        async def handler(decoded, context, resource):
            self.calls.append((version, node, "handler"))
            assert resource is self.resources[(version, node)]
            result = {"version": version, "count": decoded["count"],
                      "actor": context.actor_id, "tenant": context.tenant_id,
                      "nested": decoded.get("nested", [])}
            self.results.append(result)
            return result

        return BindingRegistration(binding_id=node, version=version, kind="registered",
            portability=Portability.from_json({"kind": "native", "language": "python",
                                               "version": "3.11", "factory": "explicit-local"}),
            effect=effect, abi=abi, artifact_digest="sha256:" + ("a" if version == "A" else "b") * 64,
            input_schema=cast(Schema, freeze_json({"type": "object", "additionalProperties": False,
                "required": ["count"] if version == "B" else [], "properties": {
                "count": {"type": "integer" if version == "B" else ["integer", "string"]},
                "nested": {"type": "array", "items": {"type": "object"}}}})),
            validator_contract_id="native-positive", decoder_contract_id="decode-int",
            default_contract_id="count-two", coercion_contract_id="int-string",
            handler=handler, decoder=decoder, validator=validator)

    @asynccontextmanager
    async def resource(self, binding, node):
        key = (binding.entry.binding.version, node)
        handle = object()
        self.resources[key] = handle
        try:
            yield handle
        finally:
            self.closed.append(key)

    def prepare(self, *, binding, node_id, context):
        return self.resource(binding, node_id)


class Provider:
    def __init__(self, execution):
        self.execution = execution
        self.registrations = [execution.registration(n, v, effect="write" if n == "w" else "read_only")
                              for v in ("A", "B") for n in ("r", "w", "p", "reserved")]
        self.registry = RegistryGeneration(build="exact", registrations=self.registrations)

    def compile(self, snapshot):
        return GraphCompiler.compile(snapshot.definition, registry=self.registry,
            compiler_contract_version="compiler-v1", compiler_build="build1",
            app_dependency_contract=AppDependencyContract("deps1", "build1"),
            environment_compatibility=("python",), locale="en")

    def verify(self, request, snapshot, context):
        plan = self.compile(snapshot)
        return VerifiedReleaseManifest("g", snapshot.revision, snapshot.definition_digest, plan.manifest)

    async def prepare(self, captured):
        plan = self.compile(captured.snapshot)
        return await prepare_generation(plan, graph_id="g", revision=captured.request.revision,
            generation_uuid=captured.generation_id, adapters={n: PreparationAdapterRegistration(
                b.entry.binding.binding_id, b.entry.binding.version, b.entry.abi,
                b.entry.contract_digest, self.execution) for n, b in plan.bindings.items()})

    def execution_adapters(self):
        return tuple(ExecutionAdapterRegistration(r.binding_id, r.binding_id, r.version,
            r.abi, r.contract_digest, PythonManagedCallAdapter()) for r in self.registrations)


class Authority:
    def __init__(self, host):
        self.host = host
        self.scope = frozenset({"r", "w", "p", "reserved"})
        self.allowed = True
        self.calls = []
        self.block_operation: str | None = None
        self.entered, self.proceed = asyncio.Event(), asyncio.Event()
        self.revision: str | None = None

    async def verify(self, request):
        self.calls.append(request)
        if self.block_operation == request.operation:
            self.block_operation = None
            self.entered.set()
            await self.proceed.wait()
        return AdmissionDecision(self.allowed, self.revision or self.host.context.authority_revision,
                                 self.scope)


def definition(provider):
    nodes = [{"node_id": r.binding_id, "kind": "tool", "name": r.binding_id,
              "description": "Business Tool", "mutable": True,
              "binding": {"kind": "registered", "binding_id": r.binding_id,
                          "version": "A", "portability": r.portability.as_json()}}
             for r in provider.registrations if r.version == "A"]
    return GraphDefinition.from_json({"schema": "contexture.graph/v1", "semantic_version": "1",
        "graph_id": "g", "application_name": "runtime-test", "mode": "runtime", "nodes": nodes,
        "roots": [{"node_id": n, "plane": "prompt" if n == "p" else "model"}
                  for n in ("r", "w", "p", "reserved")],
        "publications": [{"kind": "prompt", "publication_id": "person-only", "name": "command",
                          "description": "User command", "opens_id": "reserved", "model_may_open": False}],
        "presentations": {"default_locale": "en", "catalogs": []}})


@pytest.fixture
def system():
    with tempfile.TemporaryDirectory(prefix="contexture-runtime-") as temp:
        with FileJournal.open(Path(temp) / "store", schema=SCHEMA) as journal:
            host, execution = Host(), Execution()
            provider = Provider(execution)
            manager = owner(journal, host, projections={PROJECTION_NAME: release_projection()})
            envelope = seed()
            envelope["seed"]["inline"] = definition(provider).as_json()
            manager.commit_bootstrap(manager.preview_bootstrap(envelope, expires_at=LATER))
            intents = ReleaseIntentCoordinator(manager, manifests=provider)
            yield manager, host, execution, provider, intents
            manager.close()


def accept(system, epoch):
    manager, _, _, provider, intents = system
    snapshot = manager.read("g")
    plan = provider.compile(snapshot)
    intents.request({"schema": "contexture.release-request/v1", "graph_id": "g", "target_id": "t",
        "revision": snapshot.revision, "definition_digest": snapshot.definition_digest,
        "manifest_digest": plan.manifest.manifest_digest, "expected_desired_epoch": str(epoch),
        "idempotency_key": f"release-{epoch}", "policy": "exact", "reason": "test",
        "approval_ref": "approved"})


async def setup(system, *, adapters=None):
    manager, host, _, provider, _ = system
    coordinator = LocalServingCoordinator(manager, graph_id="g", target_id="t", instance_id="local",
        boot_id=uid(900), provider=provider)
    accept(system, 0)
    assert (await coordinator.prepare(attempt_id=uid(1), generation_id=uid(101))).status == "local_observed"
    authority = Authority(host)
    runtime = ManagedRuntime(coordinator, contexts=host, authority=authority,
        adapters=provider.execution_adapters() if adapters is None else adapters)
    return coordinator, authority, runtime


def ref(node="r", generation=101, graph="g"):
    return str(ManagedReference(graph, uid(generation), node))


def json_output(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def change(system, commands):
    manager = system[0]
    request = {"schema": "contexture.transaction/v1", "graph_id": "g",
        "base_revision": manager.read("g").revision, "idempotency_key": "change",
        "reason": "test", "correlation_id": "trace", "commands": commands}
    manager.commit(manager.preview(request, expires_at=LATER))


async def swap(system, coordinator):
    accept(system, 1)
    assert (await coordinator.prepare(attempt_id=uid(2), generation_id=uid(102))).status == "local_observed"


@pytest.mark.asyncio
async def test_real_json_defaults_coercion_native_validation_and_write(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            assert request.generation_uuid == uid(101)
            assert json_output(await request.invoke_read_only(ref()))["count"] == 2
            assert json_output(await request.invoke_read_only(ref(), {"count": "3"}))["count"] == 3
            assert json_output(await request.invoke(ref("w"), {"count": 4}))["count"] == 4
            assert [x.operation for x in authority.calls] == [
                "request", "invoke_read_only", "invoke_read_only", "invoke"]
            assert all(x.current_index.definition.graph_id == "g" for x in authority.calls)
            assert system[2].calls == [("A", n, stage) for n in ("r", "r", "w")
                                      for stage in ("decoder", "validator", "handler")]
            before = len(system[2].calls)
            with pytest.raises(GraphError, match="Negative"):
                await request.invoke_read_only(ref(), {"count": "-1"})
            assert system[2].calls[before:] == [("A", "r", "decoder"), ("A", "r", "validator")]
            result = json_output(await request.invoke_read_only(ref(), {"nested": [{"n": 1}]}))
            result["nested"][0]["n"] = 9
            assert system[2].results[-1]["nested"] == [{"n": 1}]
        with pytest.raises(GraphError, match="closed"):
            await request.invoke_read_only(ref())
    finally:
        await coordinator.shutdown()
    assert len(system[2].closed) == 4


@pytest.mark.asyncio
async def test_projection_snapshot_is_exact_immutable_current_and_has_no_execution(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request(selection=frozenset({"r", "w"})) as request:
            snapshot = await request.projection_snapshot()
            assert snapshot.revision == "1"
            assert snapshot.generation_uuid == uid(101)
            assert snapshot.plan.definition.graph_id == "g"
            assert snapshot.context == system[1].context
            assert snapshot.authorized_ids == frozenset({"r", "w"})
            authority.scope = frozenset({"r"})
            attenuated = await request.projection_snapshot()
            assert attenuated.plan is snapshot.plan
            assert attenuated.authorized_ids == frozenset({"r"})
            assert [call.operation for call in authority.calls] == ["request", "projection", "projection"]
            with pytest.raises(AttributeError):
                setattr(snapshot, "revision", "9")
        with pytest.raises(GraphError, match="closed"):
            await request.projection_snapshot()
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_projection_snapshot_pins_old_plan_but_checks_new_active_ids(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            before = await request.projection_snapshot()
            change(system, [{"op": "delete_nodes", "targets": ["w"], "deleted_ids": ["w"],
                "policy": "reject_references", "disconnections": [
                    {"source_id": "g", "target_id": "w", "relation": "containment"}]}])
            await swap(system, coordinator)
            after = await request.projection_snapshot()
            assert after.plan is before.plan
            assert after.generation_uuid == before.generation_uuid
            assert after.revision == before.revision
            assert "w" not in after.authorized_ids
            assert authority.calls[-1].current_index.definition is not before.plan.definition
            async with runtime.request() as fresh:
                current = await fresh.projection_snapshot()
                assert current.generation_uuid == uid(102)
                assert current.revision == "2"
                assert "w" not in current.plan.definition.nodes
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_projection_snapshot_refuses_child_task_and_current_revocation(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            with pytest.raises(GraphError, match="another task"):
                await asyncio.create_task(request.projection_snapshot())
            authority.allowed = False
            with pytest.raises(GraphError):
                await request.projection_snapshot()
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("reference,write,arguments", [
    ("/r", False, {}), ("r", False, {}), (ref(graph="other"), False, {}),
    (ref(generation=777), False, {}), (ref("missing"), False, {}),
    (ref("p"), False, {}), (ref("reserved"), False, {}),
    (ref("w"), False, {}), (ref(), True, {}),
    (ref(), False, {"unknown": True}), (ref(), False, {"count": []}),
    (ref() + ":alias", False, {}), (ref(), False, {"tenant_id": "other"}),
    (ref(), False, {"selection": ["w"]}), (ref(), False, {"input_contract": True}),
    (ref(), False, {"count": object()}),
])
async def test_ref_plane_door_schema_payload_forgery_refuse_before_native_calls(
        system, reference, write, arguments):
    coordinator, _, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            with pytest.raises(GraphError):
                await (request.invoke(reference, arguments) if write else
                       request.invoke_read_only(reference, arguments))
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("change_identity", ["actor_id", "tenant_id", "authority_revision"])
async def test_current_identity_and_revision_not_payload_or_old_lease(system, change_identity):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            if change_identity == "authority_revision":
                authority.revision = "1"
                system[1].context = replace(system[1].context, authority_revision="2")
            else:
                system[1].context = replace(system[1].context, **{change_identity: "other"})
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref())
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_initial_ceiling_intersects_selection_and_current_permission(system):
    coordinator, authority, runtime = await setup(system)
    try:
        authority.scope = frozenset({"r"})
        async with runtime.request() as request:
            authority.scope = frozenset({"r", "w"})
            with pytest.raises(GraphError):
                await request.invoke(ref("w"))
            await request.invoke_read_only(ref())
        async with runtime.request(selection=frozenset({"r"})) as request:
            with pytest.raises(GraphError):
                await request.invoke(ref("w"))
        async with runtime.request(selection=frozenset()) as request:
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref())
        authority.allowed = False
        with pytest.raises(GraphError):
            async with runtime.request():
                pytest.fail("Denied request admitted")
        assert [x for x in system[2].calls if x[2] == "handler"] == [("A", "r", "handler")]
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("during_await", [False, True])
async def test_current_revoke_even_with_pinned_resource_lease(system, during_await):
    coordinator, authority, runtime = await setup(system)
    try:
        async def revoke():
            await authority.entered.wait()
            authority.scope = frozenset()
            authority.proceed.set()

        async with runtime.request() as request:
            if during_await:
                authority.block_operation = "invoke_read_only"
                task = asyncio.create_task(revoke())
            else:
                authority.scope = frozenset()
                task = None
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref())
            if task:
                await task
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_detach_arguments_before_authority_await_and_current_context(system):
    coordinator, authority, runtime = await setup(system)
    try:
        raw = {"count": "5", "nested": [{"n": 1}]}
        async def mutate():
            await authority.entered.wait()
            raw["nested"][0]["n"] = 99
            raw["count"] = "999"
            authority.proceed.set()

        async with runtime.request() as request:
            authority.block_operation = "invoke_read_only"
            task = asyncio.create_task(mutate())
            result = json_output(await request.invoke_read_only(ref(), raw))
            await task
            assert result["nested"] == [{"n": 1}] and result["count"] == 5
            assert raw["count"] == "999"
    finally:
        await coordinator.shutdown()


def binding_switch(system):
    node = system[0].read("g").definition.nodes["r"].as_json()
    binding = {**node["binding"], "version": "B"}
    change(system, [{"op": "update_node", "node_id": "r", "set_fields": {"binding": binding},
                     "unset_fields": []}])


@pytest.mark.asyncio
@pytest.mark.parametrize("await_stage", ["request", "invoke_read_only", "captured"])
async def test_swap_during_auth_pins_complete_A_and_new_request_rejects_A(system, await_stage):
    coordinator, authority, runtime = await setup(system)
    try:
        async def release_B():
            await authority.entered.wait()
            binding_switch(system)
            await swap(system, coordinator)
            authority.proceed.set()

        task = None
        if await_stage == "request":
            authority.block_operation = "request"
            task = asyncio.create_task(release_B())
        async with runtime.request() as request:
            if await_stage == "captured":
                binding_switch(system)
                await swap(system, coordinator)
            elif await_stage == "invoke_read_only":
                authority.block_operation = "invoke_read_only"
                task = asyncio.create_task(release_B())
            result = json_output(await request.invoke_read_only(ref(), {"count": "6"}))
            if task:
                await task
            assert request.generation_uuid == uid(101) and result["version"] == "A"
            assert ("A", "r") not in system[2].closed
            assert authority.calls[-1].current_index.definition.nodes["r"].binding.version == "B"
        async with runtime.request() as request:
            with pytest.raises(GraphError) as error:
                await request.invoke_read_only(ref())
            assert error.value.code == "stale_reference"
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref(generation=102), {"count": "6"})
            assert json_output(await request.invoke_read_only(ref(generation=102), {"count": 7}))["version"] == "B"
        assert [x for x in system[2].calls if x[2] == "handler"] == [
            ("A", "r", "handler"), ("B", "r", "handler")]
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("during_await", [False, True])
async def test_current_active_deletion_denies_old_request_even_lying_authority(system, during_await):
    coordinator, authority, runtime = await setup(system)
    try:
        async def delete_and_swap():
            if during_await:
                await authority.entered.wait()
            change(system, [{"op": "delete_nodes", "targets": ["r"], "deleted_ids": ["r"],
                "policy": "reject_references", "disconnections": [
                    {"source_id": "g", "target_id": "r", "relation": "containment"}]}])
            await swap(system, coordinator)
            authority.proceed.set()

        async with runtime.request() as request:
            if during_await:
                authority.block_operation = "invoke_read_only"
                task = asyncio.create_task(delete_and_swap())
            else:
                await delete_and_swap()
                task = None
            assert "r" in authority.scope
            with pytest.raises(GraphError):
                await request.invoke_read_only(ref())
            if task:
                await task
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["node_id", "binding_id", "version", "abi", "contract_digest", "missing"])
async def test_all_adapters_preflight_before_any_execution(system, field):
    registrations = list(system[3].execution_adapters())
    if field == "missing":
        registrations = [r for r in registrations if r.node_id != "w"]
    else:
        index = next(i for i, r in enumerate(registrations) if r.node_id == "w" and r.version == "A")
        registrations[index] = replace(registrations[index], **{field: "wrong"})
    coordinator, _, runtime = await setup(system, adapters=tuple(registrations))
    try:
        with pytest.raises(GraphError, match="adapter"):
            async with runtime.request():
                pytest.fail("Incomplete preflight admitted")
        assert system[2].calls == []
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_new_boot_refusal_and_no_implicit_factory_or_ABI_dispatch(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            # A reference to a prior boot's generation is not repaired by ID.
            with pytest.raises(GraphError, match="generation"):
                await request.invoke_read_only(ref(generation=901))
        system[3].registry = RegistryGeneration(build="replacement", registrations=[])
        # Exact already captured decoder/validator/handler still execute, without
        # consulting the changed latest registry or invoking any factory.
        async with runtime.request() as request:
            assert json_output(await request.invoke_read_only(ref()))["version"] == "A"
        b = system[3].registrations[0]
        b = replace(b, abi="opaque-any", factory=lambda: pytest.fail("Factory called"))
        async with coordinator.request_lease() as lease:
            pinned = lease.generation.plan.bindings["r"]
        with pytest.raises(GraphError, match="ABI"):
            await PythonManagedCallAdapter().call(
                replace(pinned, registration=b), {}, system[1].context, object())
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_real_new_boot_does_not_accept_old_ref(system):
    coordinator, _, runtime = await setup(system)
    await coordinator.shutdown()
    fresh = LocalServingCoordinator(system[0], graph_id="g", target_id="t", instance_id="local",
        boot_id=uid(999), provider=system[3])
    try:
        assert (await fresh.prepare(attempt_id=uid(3), generation_id=uid(103))).status == "local_observed"
        runtime = ManagedRuntime(fresh, contexts=system[1], authority=Authority(system[1]),
                                 adapters=system[3].execution_adapters())
        async with runtime.request() as request:
            with pytest.raises(GraphError) as error:
                await request.invoke_read_only(ref())
            assert error.value.code == "stale_reference"
            assert json_output(await request.invoke_read_only(ref(generation=103)))["version"] == "A"
    finally:
        await fresh.shutdown()


@pytest.mark.asyncio
async def test_context_switch_during_authority_await_denied_before_decoder(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async def switch_identity():
            await authority.entered.wait()
            system[1].context = replace(system[1].context, tenant_id="other")
            authority.proceed.set()

        async with runtime.request() as request:
            authority.block_operation = "invoke_read_only"
            task = asyncio.create_task(switch_identity())
            with pytest.raises(GraphError, match="Authority changed"):
                await request.invoke_read_only(ref())
            await task
        assert not system[2].calls
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_cancelled_authority_releases_pin_and_cross_task_cannot_escape(system):
    coordinator, authority, runtime = await setup(system)
    try:
        async with runtime.request() as request:
            with pytest.raises(GraphError, match="another task"):
                await asyncio.create_task(request.invoke_read_only(ref()))
        authority.block_operation = "request"
        async def blocked_request():
            async with runtime.request():
                pytest.fail("Cancelled request admitted")
        task = asyncio.create_task(blocked_request())
        await authority.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not system[2].calls
    finally:
        await coordinator.shutdown()
    assert len(system[2].closed) == 4


@pytest.mark.parametrize("revision", ["01", "-1", "1.0", "100000000000000000000", 1])
def test_authority_revision_uses_canonical_policy_counters(revision):
    with pytest.raises(GraphError):
        AdmissionDecision(True, revision, frozenset({"r"}))


@pytest.mark.asyncio
async def test_exact_opaque_secondary_adapter_is_preflighted_not_inferred(system):
    provider = system[3]
    provider.registrations = [replace(r, abi="opaque-any") if r.binding_id == "w" else r
                              for r in provider.registrations]
    provider.registry = RegistryGeneration(build="opaque", registrations=provider.registrations)
    coordinator, _, runtime = await setup(system)
    try:
        # Even invoking r is refused when another resolved node has a Python
        # adapter that cannot implement its explicitly attested opaque ABI.
        with pytest.raises(GraphError, match="ABI"):
            async with runtime.request():
                pytest.fail("Unsupported secondary ABI passed preflight")
        assert not system[2].calls
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_manager_captures_at_enter_not_construction(system):
    coordinator, _, runtime = await setup(system)
    try:
        manager = runtime.request()
        binding_switch(system)
        await swap(system, coordinator)
        async with manager as request:
            assert request.generation_uuid == uid(102)
            with pytest.raises(GraphError) as error:
                await request.invoke_read_only(ref())
            assert error.value.code == "stale_reference"
            assert json_output(await request.invoke_read_only(ref(generation=102), {"count": 1}))["version"] == "B"
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_explicit_opaque_adapter_receives_exact_captured_binding_and_resource(system):
    coordinator, authority, _ = await setup(system)
    received = []
    class ExplicitAdapter:
        async def call(self, binding, arguments, context, resource):
            received.append((binding, arguments, context, resource))
            return {"explicit": True}

    registrations = tuple(replace(r, adapter=ExplicitAdapter()) for r in system[3].execution_adapters())
    runtime = ManagedRuntime(coordinator, contexts=system[1], authority=authority, adapters=registrations)
    try:
        async with coordinator.request_lease() as lease:
            exact = lease.generation.plan.bindings["r"]
            resource = lease.handles["r"]
        async with runtime.request() as request:
            assert await request.invoke_read_only(ref(), {"nested": [{"n": 1}]}) == {"explicit": True}
        assert received[0][0] is exact and received[0][3] is resource
        with pytest.raises(TypeError):
            received[0][1]["nested"][0]["n"] = 2
        assert not system[2].calls  # no implicit decoder/validator/handler ABI
    finally:
        await coordinator.shutdown()