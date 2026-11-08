"""Real stdio smoke fixture; application startup/cleanup is not shipped server code."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
import json
from pathlib import Path
import tempfile
from typing import cast

from contexture.application.managed import GraphOwner
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
    AppDependencyContract, BindingRegistration, RegistryGeneration, Schema,
)
from contexture.core.graph.canonical import freeze_json
from contexture.core.graph.compiler import GraphCompiler
from contexture.core.graph.model import GraphDefinition, Portability
from contexture.core.graph.policy import AdmissionDecision, VerifiedApproval, VerifiedContext
from contexture.server.managed import ManagedMCPServer
from contexture.server.managed_journal import FileJournal

NOW = "2026-11-05T00:00:00Z"
LATER = "2026-11-05T01:00:00Z"


class Host:
    """Single-task local authority, not a pytest fixture or production identity."""

    def __init__(self):
        self.context = VerifiedContext("actor", "tenant", "1",
            frozenset({"graph.roots", "graph.bootstrap"}))

    def current(self):
        return self.context

    def actor_ref(self, context):
        return f"{context.tenant_id}:{context.actor_id}"

    @contextmanager
    def hold(self, context):
        assert context == self.context
        yield

    def decide(self, request):
        return AdmissionDecision(True, self.context.authority_revision,
            frozenset((*request.impact, *request.reference_targets)))

    async def verify(self, request):
        return AdmissionDecision(True, self.context.authority_revision, frozenset({"r", "w"}))


class Approvals:
    def verify(self, context, reference, subject_digest, now):
        return VerifiedApproval(context.actor_id, context.tenant_id,
            context.authority_revision, subject_digest, LATER)


class Provider:
    """Two native bindings compiled and prepared through the real application."""

    def __init__(self):
        def decode(arguments):
            arguments.setdefault("count", 2)
            arguments["count"] = int(arguments["count"])
            return arguments

        async def handler(decoded, context, resource):
            return {"count": decoded["count"]}

        self.registrations = [BindingRegistration(
            binding_id=node, version="A", kind="registered",
            portability=Portability.from_json({"kind": "native", "language": "python",
                "version": "3.11", "factory": "explicit-local"}),
            effect="write" if node == "w" else "read_only",
            abi="python-managed-call/v1", artifact_digest="sha256:" + "a" * 64,
            input_schema=cast(Schema, freeze_json({"type": "object", "additionalProperties": False,
                "properties": {"count": {"type": ["integer", "string"]}}})),
            validator_contract_id="native-positive", decoder_contract_id="decode-int",
            default_contract_id="count-two", coercion_contract_id="int-string",
            handler=handler, decoder=decode, validator=lambda decoded: None) for node in ("r", "w")]
        self.registry = RegistryGeneration(build="exact", registrations=self.registrations)

    def compile(self, snapshot):
        return GraphCompiler.compile(snapshot.definition, registry=self.registry,
            compiler_contract_version="compiler-v1", compiler_build="build1",
            app_dependency_contract=AppDependencyContract("deps1", "build1"),
            environment_compatibility=("python",), locale="en")

    def verify(self, request, snapshot, context):
        plan = self.compile(snapshot)
        return VerifiedReleaseManifest("g", snapshot.revision, snapshot.definition_digest, plan.manifest)

    @asynccontextmanager
    async def resource(self):
        yield object()

    async def prepare(self, captured):
        plan = self.compile(captured.snapshot)
        return await prepare_generation(plan, graph_id="g", revision=captured.request.revision,
            generation_uuid=captured.generation_id, adapters={n: PreparationAdapterRegistration(
                b.entry.binding.binding_id, b.entry.binding.version, b.entry.abi,
                b.entry.contract_digest, Resources(self)) for n, b in plan.bindings.items()})


class Resources:
    def __init__(self, provider):
        self.provider = provider

    def prepare(self, *, binding, node_id, context):
        return self.provider.resource()


async def main() -> None:
    # Build the actual journal/compiler/runtime before serving; never fake readiness.
    schema = json.loads((Path(__file__).resolve().parents[1]
        / "spec/managed/schemas/managed.schema.json").read_text())
    with tempfile.TemporaryDirectory(prefix="contexture-mcp-") as temp:
        with FileJournal.open(Path(temp) / "store", schema=schema) as journal:
            host, provider = Host(), Provider()
            manager = GraphOwner(journal, identities=host, admission=host, authority_fence=host,
                clock=lambda: NOW, approvals=Approvals(),
                projections={PROJECTION_NAME: release_projection()})
            coordinator = None
            try:
                definition = GraphDefinition.from_json({
                    "schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": "g",
                    "application_name": "stdio-smoke", "mode": "runtime",
                    "nodes": [{"node_id": r.binding_id, "kind": "tool", "name": r.binding_id,
                        "description": "Business Tool", "mutable": True,
                        "binding": {"kind": "registered", "binding_id": r.binding_id,
                            "version": "A", "portability": r.portability.as_json()}}
                        for r in provider.registrations],
                    "roots": [{"node_id": n, "plane": "model"} for n in ("r", "w")],
                    "presentations": {"default_locale": "en", "catalogs": []}})
                manager.commit_bootstrap(manager.preview_bootstrap({
                    "schema": "contexture.bootstrap/v1", "graph_id": "g", "expected_revision": "0",
                    "seed": {"inline": definition.as_json()}, "idempotency_key": "seed-g",
                    "approval_ref": "approved", "reason": "stdio smoke",
                    "correlation_id": "trace"}, expires_at=LATER))
                snapshot = manager.read("g")
                plan = provider.compile(snapshot)
                ReleaseIntentCoordinator(manager, manifests=provider).request({
                    "schema": "contexture.release-request/v1", "graph_id": "g", "target_id": "t",
                    "revision": snapshot.revision, "definition_digest": snapshot.definition_digest,
                    "manifest_digest": plan.manifest.manifest_digest, "expected_desired_epoch": "0",
                    "idempotency_key": "release-0", "policy": "exact", "reason": "stdio smoke",
                    "approval_ref": "approved"})
                coordinator = LocalServingCoordinator(manager, graph_id="g", target_id="t",
                    instance_id="local", boot_id="00000000-0000-0000-0000-000000000900",
                    provider=provider)
                prepared = await coordinator.prepare(
                    attempt_id="00000000-0000-0000-0000-000000000001",
                    generation_id="00000000-0000-0000-0000-000000000101")
                assert prepared.status == "local_observed", prepared
                runtime = ManagedRuntime(coordinator, contexts=host, authority=host,
                    adapters=tuple(ExecutionAdapterRegistration(r.binding_id, r.binding_id,
                        r.version, r.abi, r.contract_digest, PythonManagedCallAdapter())
                        for r in provider.registrations))
                await ManagedMCPServer(runtime).build().run_stdio_async()
            finally:
                if coordinator is not None:
                    await coordinator.shutdown()
                manager.close()


if __name__ == "__main__":
    asyncio.run(main())