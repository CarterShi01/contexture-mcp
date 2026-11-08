"""One supported composition root for a managed (dynamic, versioned) graph.

``ManagedGraphHost`` wires the parts a Host would otherwise assemble by hand:

* a durable single-owner ``FileJournal`` (with the packaged schema),
* a ``GraphOwner`` for previewed, admitted, idempotent graph transactions,
* registry-backed publication evidence, compilation and resource preparation,
* a ``ReleaseManager`` (desired release -> prepare -> atomic local swap), and
* a ``ManagedRuntime`` whose requests pin one immutable serving generation.

Governance is never defaulted: the caller supplies a ``HostPolicy`` (identity,
authority fence, admission, approvals and per-request authority). For local
development and demos, :class:`SingleActorPolicy` is an explicit, clearly named
allow-all policy for one trusted actor; never use it for a shared deployment.

Typical loop::

    async with ManagedGraphHost.open(store, graph_id="g", policy=policy,
                                     registry=registry) as host:
        host.bootstrap(seed_definition)
        await host.publish()                     # serve revision 1
        host.change({"op": "create_node", ...},  # edit the tree
                    {"op": "attach_node", ...}, reason="add a Skill")
        await host.publish()                     # hot swap; old requests drain
        async with host.runtime.request() as request:
            await request.discover()
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Protocol, cast
from uuid import uuid4

from contexture.application.managed import GraphOwner
from contexture.application.managed_generation import (
    PreparationAdapterRegistration, PreparationContext, ReadyGeneration, prepare_generation,
)
from contexture.application.managed_release_manager import ReleaseManager, ReleaseWaitResult
from contexture.application.managed_releases import (
    PROJECTION_NAME, VerifiedReleaseManifest, release_projection,
)
from contexture.application.managed_runtime import (
    ExecutionAdapter, ExecutionAdapterRegistration, ManagedRuntime, PythonManagedCallAdapter,
    RequestAuthorization,
)
from contexture.application.managed_serving import CapturedRelease
from contexture.core.graph.bindings import (
    AppDependencyContract, BindingDescriptor, BindingRegistration, Effect, RegistryGeneration,
    ResolvedBinding,
)
from contexture.core.graph.canonical import canonical_json
from contexture.core.graph.model import Portability
from contexture.core.graph.bundles import GraphBundle, HistoryBundle, bootstrap_definition
from contexture.core.graph.compiler import GraphCompiler, StructuralPlan
from contexture.core.graph.diff import DefinitionDiff
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import RevisionState
from contexture.core.graph.history import History, RevisionRecord
from contexture.core.graph.merge import MergeResult
from contexture.core.graph.model import GraphDefinition, PublicationContract
from contexture.core.graph.policy import (
    AdmissionDecision, AdmissionRequest, VerifiedApproval, VerifiedContext,
)
from contexture.core.graph.releases import ReleaseReceipt, ReleaseRequest
from contexture.server.managed_journal import FileJournal

__all__ = [
    "CompilerProfile", "HostPolicy", "ManagedGraphHost", "SingleActorPolicy", "python_tool",
    "tool_binding", "utc_clock",
]

_LOG = logging.getLogger("contexture.managed.host")


def utc_clock() -> str:
    """Second-precision UTC RFC 3339 timestamp, the managed wire Timestamp shape."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _later(now: str, seconds: int) -> str:
    moment = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (moment + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


_PYTHON = Portability.from_json({"kind": "native", "language": "python",
                                 "version": "3", "factory": "explicit-local"})


def python_tool(binding_id: str, handler: Callable[..., Any], *, version: str = "1",
                input_schema: Mapping[str, Any] | None = None, effect: Effect = "read_only",
                artifact: str | None = None) -> BindingRegistration:
    """Register a trusted ``async def handler(arguments, context, resource)``.

    Arguments are validated against ``input_schema`` (default: an object with
    no properties) and passed unchanged. ``artifact`` identifies the deployed
    code (e.g. a package version); it is digested into the contract, so a new
    artifact is a new contract. Bump ``version`` for a behavior change that
    graph authors must opt into by upgrading the Tool's binding.
    """
    schema = dict(input_schema or {"type": "object", "additionalProperties": False})
    identity = artifact or f"{getattr(handler, '__module__', '?')}:{getattr(handler, '__qualname__', '?')}"

    def decode(arguments: Any) -> Any:
        return arguments

    def validate(decoded: Any) -> None:
        return None

    return BindingRegistration(binding_id=binding_id, version=version, kind="registered",
        portability=_PYTHON, effect=effect, abi="python-managed-call/v1",
        artifact_digest="sha256:" + hashlib.sha256(
            canonical_json({"artifact": identity, "version": version})).hexdigest(),
        input_schema=schema, validator_contract_id="json-schema", decoder_contract_id="identity",
        default_contract_id="none", coercion_contract_id="none",
        handler=handler, decoder=decode, validator=validate,
        argument_free=_accepts_empty(schema))


def _accepts_empty(schema: Mapping[str, Any]) -> bool:
    from jsonschema import Draft202012Validator

    return Draft202012Validator(dict(schema)).is_valid({})


def tool_binding(registration: BindingRegistration) -> dict[str, Any]:
    """The binding descriptor a Tool node uses to reference ``registration``."""
    return {"kind": registration.kind, "binding_id": registration.binding_id,
            "version": registration.version, "portability": registration.portability.as_json()}


class HostPolicy(Protocol):
    """Every governance decision a managed Host makes, in one trusted object.

    All methods must consult CURRENT authority; nothing here may be derived from
    request payloads. ``hold`` must fence authority changes through
    decision+append (see ``AuthorityFence``).
    """

    def current(self) -> VerifiedContext:
        """The authenticated caller of the current operation."""
        ...

    def actor_ref(self, context: VerifiedContext) -> str:
        """Stable store-wide tenant+actor reference (idempotency scope)."""
        ...

    def hold(self, context: VerifiedContext) -> AbstractContextManager[None]:
        """Authority fence held across admission and journal append."""
        ...

    def decide(self, request: AdmissionRequest) -> AdmissionDecision:
        """Graph read/mutation admission over exact impact and reference targets."""
        ...

    def verify_approval(self, context: VerifiedContext, reference: str,
                        subject_digest: str, now: str) -> VerifiedApproval:
        """Verify a Host approval proof for one exact preview subject."""
        ...

    async def authorize_request(self, request: RequestAuthorization) -> AdmissionDecision:
        """Per-request runtime authority over stable IDs in the active index."""
        ...


class SingleActorPolicy:
    """DEVELOPMENT ONLY: one trusted local actor may do everything.

    Grants every admission, approval and runtime request for a single fixed
    actor (including ``graph.roots``/``graph.bootstrap``). Use it for demos,
    tests and single-user local tools; supply a real ``HostPolicy`` otherwise.
    """

    def __init__(self, actor_id: str = "developer", tenant_id: str = "local") -> None:
        self.context = VerifiedContext(actor_id, tenant_id, "1",
                                       frozenset({"graph.roots", "graph.bootstrap"}))
        self._fence = RLock()

    def current(self) -> VerifiedContext:
        return self.context

    def actor_ref(self, context: VerifiedContext) -> str:
        return f"{context.tenant_id}:{context.actor_id}"

    @contextmanager
    def hold(self, context: VerifiedContext) -> Iterator[None]:
        with self._fence:
            yield

    def decide(self, request: AdmissionRequest) -> AdmissionDecision:
        return AdmissionDecision(True, self.context.authority_revision,
                                 frozenset((*request.impact, *request.reference_targets)))

    def verify_approval(self, context: VerifiedContext, reference: str,
                        subject_digest: str, now: str) -> VerifiedApproval:
        return VerifiedApproval(context.actor_id, context.tenant_id, context.authority_revision,
                                subject_digest, _later(now, 3600))

    async def authorize_request(self, request: RequestAuthorization) -> AdmissionDecision:
        return AdmissionDecision(True, self.context.authority_revision,
                                 frozenset(request.current_index.definition.nodes))


@dataclass(frozen=True, slots=True)
class CompilerProfile:
    """Explicit compile inputs pinned into every execution manifest."""

    app_dependency: AppDependencyContract
    compiler_contract_version: str = "contexture-compiler/v1"
    compiler_build: str = "contexture-python"
    environment: tuple[str, ...] = ("python",)
    locale: str = "en"


class _NoResource:
    """Preparation adapter for stateless bindings: acquires nothing."""

    @asynccontextmanager
    async def _none(self) -> AsyncIterator[object]:
        yield None

    def prepare(self, *, binding: ResolvedBinding, node_id: str,
                context: PreparationContext) -> AbstractAsyncContextManager[object]:
        return self._none()


@dataclass(slots=True)
class _Provider:
    """Registry-backed manifest verification, compilation and preparation."""

    graph_id: str
    registry: RegistryGeneration | None
    profile: CompilerProfile
    resources: Mapping[tuple[str, str], Any] = field(default_factory=dict)

    def compile(self, definition: GraphDefinition) -> StructuralPlan:
        return GraphCompiler.compile(definition, registry=self.registry,
            compiler_contract_version=self.profile.compiler_contract_version,
            compiler_build=self.profile.compiler_build,
            app_dependency_contract=self.profile.app_dependency,
            environment_compatibility=self.profile.environment, locale=self.profile.locale)

    def contract(self, binding: BindingDescriptor) -> PublicationContract | None:
        if self.registry is None:
            return None
        try:
            return self.registry.resolve(binding).publication_contract
        except GraphError:
            return None

    def verify(self, request: ReleaseRequest, snapshot: RevisionState,
               context: VerifiedContext) -> VerifiedReleaseManifest:
        plan = self.compile(snapshot.definition)
        return VerifiedReleaseManifest(self.graph_id, snapshot.revision,
                                       snapshot.definition_digest, plan.manifest)

    async def prepare(self, captured: CapturedRelease) -> ReadyGeneration:
        plan = self.compile(captured.snapshot.definition)
        adapters = {node_id: PreparationAdapterRegistration(
            b.entry.binding.binding_id, b.entry.binding.version, b.entry.abi, b.entry.contract_digest,
            self.resources.get((b.entry.binding.binding_id, b.entry.binding.version), _NoResource()))
            for node_id, b in plan.bindings.items()}
        return await prepare_generation(plan, graph_id=self.graph_id,
            revision=captured.request.revision, generation_uuid=captured.generation_id,
            adapters=adapters)


class _Approvals:
    def __init__(self, policy: HostPolicy) -> None:
        self._policy = policy

    def verify(self, context: VerifiedContext, reference: str, subject_digest: str,
               now: str) -> VerifiedApproval:
        return self._policy.verify_approval(context, reference, subject_digest, now)


class _RequestAuthority:
    def __init__(self, policy: HostPolicy) -> None:
        self._policy = policy

    async def verify(self, request: RequestAuthorization) -> AdmissionDecision:
        return await self._policy.authorize_request(request)


class ManagedGraphHost:
    """A running managed graph: transactions, history, releases and requests.

    Construct with :meth:`open`. Mutations are synchronous owner transactions;
    serving changes only through :meth:`publish` (an explicit release), so a
    committed definition is never mistaken for an active one.
    """

    def __init__(self, journal: FileJournal, owner: GraphOwner, provider: _Provider, *,
                 policy: HostPolicy, graph_id: str, target_id: str, instance_id: str,
                 registrations: tuple[ExecutionAdapterRegistration, ...],
                 clock: Callable[[], str], preview_seconds: int, prepare_timeout: float,
                 snapshot_every: int = 1000) -> None:
        self.journal, self.owner = journal, owner
        self.graph_id, self.target_id, self._instance_id = graph_id, target_id, instance_id
        self._provider, self._policy, self._registrations = provider, policy, registrations
        self._clock, self._preview_seconds, self._prepare_timeout = clock, preview_seconds, prepare_timeout
        self._releases: ReleaseManager | None = None
        self._runtime: ManagedRuntime | None = None
        self._snapshot_every = snapshot_every

    @property
    def releases(self) -> ReleaseManager:
        if self._releases is None:
            raise GraphError("not_ready", "Serving starts after the graph is bootstrapped")
        return self._releases

    @property
    def runtime(self) -> ManagedRuntime:
        if self._runtime is None:
            raise GraphError("not_ready", "Serving starts after the graph is bootstrapped")
        return self._runtime

    def _start_serving(self) -> None:
        # The serving coordinator binds to the store origin (first record).
        self._releases = ReleaseManager(self.owner, graph_id=self.graph_id,
            target_id=self.target_id, instance_id=self._instance_id, boot_id=str(uuid4()),
            provider=self._provider, prepare_timeout=self._prepare_timeout)
        self._runtime = ManagedRuntime(self._releases.serving, contexts=self._policy,
            authority=_RequestAuthority(self._policy), adapters=self._registrations)

    @classmethod
    @asynccontextmanager
    async def open(cls, store: Path | str, *, graph_id: str, policy: HostPolicy,
                   registry: RegistryGeneration | None = None,
                   profile: CompilerProfile | None = None, target_id: str = "local",
                   instance_id: str = "local", durability: str = "strict",
                   resources: Mapping[tuple[str, str], Any] | None = None,
                   execution_adapters: Mapping[str, ExecutionAdapter] | None = None,
                   clock: Callable[[], str] = utc_clock, preview_seconds: int = 300,
                   prepare_timeout: float = 30, snapshot_every: int = 1000,
                   ) -> AsyncIterator[ManagedGraphHost]:
        """Open (or create) a store and start serving its desired release, if any.

        ``registry`` lists every deployed executable binding (None for a
        disclosure-only graph). ``resources`` maps (binding_id, version) to a
        ResourcePreparationAdapter for stateful bindings. ``execution_adapters``
        maps an ABI to its ExecutionAdapter; ``python-managed-call/v1`` is built in.
        Every ``snapshot_every`` records (and on a clean close) an owner snapshot
        is written so startup replays only the journal tail (0 disables).
        Bindings are attested per exact contract, not per node, so Tools created
        at runtime against a deployed binding execute without a restart.
        """
        profile = profile or CompilerProfile(AppDependencyContract("application", "local"))
        provider = _Provider(graph_id, registry, profile, dict(resources or {}))
        adapters: dict[str, ExecutionAdapter] = {"python-managed-call/v1": PythonManagedCallAdapter()}
        adapters.update(execution_adapters or {})
        registrations = tuple(ExecutionAdapterRegistration(None, r.binding_id, r.version, r.abi,
            r.contract_digest, adapters[r.abi]) for r in (registry.registrations.values() if registry else ())
            if r.abi in adapters)
        journal = FileJournal.open(store, durability=durability)
        owner: GraphOwner | None = None
        host: ManagedGraphHost | None = None
        try:
            owner = GraphOwner(journal, identities=policy, admission=policy, authority_fence=policy,
                clock=clock, approvals=_Approvals(policy), publication_contracts=provider.contract,
                projections={PROJECTION_NAME: release_projection()})
            host = cls(journal, owner, provider, policy=policy, graph_id=graph_id,
                       target_id=target_id, instance_id=instance_id, registrations=registrations,
                       clock=clock, preview_seconds=preview_seconds, prepare_timeout=prepare_timeout,
                       snapshot_every=snapshot_every)
            _LOG.info("managed host opened", extra={"graph_id": graph_id, "target_id": target_id,
                      "watermark": journal.watermark})
            if host.initialized:
                host._start_serving()
                if host.releases.status().desired_epoch is not None:
                    host.releases.retry_desired()  # resume serving the durable desired release
            yield host
        finally:
            if host is not None and host._releases is not None:
                await host._releases.shutdown()
            if host is not None and owner is not None:
                host._snapshot(force=True)
            if owner is not None:
                owner.close()
            journal.close()
            _LOG.info("managed host closed", extra={"graph_id": graph_id})

    # -- definition -----------------------------------------------------

    @property
    def initialized(self) -> bool:
        try:
            self.owner.read(self.graph_id)
        except GraphError as error:
            if error.code in ("graph_not_found", "reference_not_found", "history_unavailable"):
                return False
            raise
        return True

    def read(self, revision: str | None = None) -> RevisionState:
        """The accepted head (or a retained revision); not necessarily serving."""
        return self.owner.read(self.graph_id, revision)

    def bootstrap(self, definition: GraphDefinition | Mapping[str, Any], *,
                  reason: str = "initial seed", approval_ref: str = "bootstrap") -> dict[str, Any]:
        """Seed revision 1 (requires host ``graph.bootstrap`` authority)."""
        seed = definition.as_json() if isinstance(definition, GraphDefinition) else dict(definition)
        envelope = {"schema": "contexture.bootstrap/v1", "graph_id": self.graph_id,
            "expected_revision": "0", "seed": {"inline": seed}, "approval_ref": approval_ref,
            "idempotency_key": f"bootstrap-{self.graph_id}", "reason": reason,
            "correlation_id": str(uuid4())}
        preview = self.owner.preview_bootstrap(envelope, expires_at=self._expires())
        receipt = cast(dict[str, Any], self.owner.commit_bootstrap(preview))
        if self._releases is None:
            self._start_serving()
        self._snapshot()
        return receipt

    def preview(self, *commands: Mapping[str, Any], reason: str,
                idempotency_key: str | None = None) -> Any:
        """Validate a transaction against the head without committing it.

        ``preview(...).as_json()`` shows the definition diff, admission impact
        and ``protected_dependents`` (locked nodes whose agent context changes).
        """
        return self.owner.preview(self._envelope(commands, reason, idempotency_key),
                                  expires_at=self._expires())

    def change(self, *commands: Mapping[str, Any], reason: str,
               idempotency_key: str | None = None, approval_ref: str | None = None) -> dict[str, Any]:
        """Preview and commit one atomic transaction; returns the command receipt."""
        preview = self.preview(*commands, reason=reason, idempotency_key=idempotency_key)
        receipt = cast(dict[str, Any], self.owner.commit(preview, approval_ref=approval_ref))
        self._snapshot()
        return receipt

    def revert(self, target_revision: str, *, node_ids: Sequence[str] | None = None,
               reason: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """Commit a NEW revision restoring (part of) a retained revision."""
        preview = self.owner.preview_revert(self.graph_id, target_revision,
            idempotency_key=idempotency_key or str(uuid4()), reason=reason,
            correlation_id=str(uuid4()), expires_at=self._expires(),
            node_ids=None if node_ids is None else tuple(node_ids))
        receipt = cast(dict[str, Any], self.owner.commit(preview))
        self._snapshot()
        return receipt

    # -- history --------------------------------------------------------

    def history(self) -> History:
        return self.owner.history(self.graph_id)

    def diff(self, before: str, after: str) -> DefinitionDiff:
        """Stable-ID definition diff between two retained revisions."""
        return self.history().diff(before, after)

    def node_history(self, node_id: str) -> tuple[RevisionRecord, ...]:
        """Revisions that changed one node (aligned by ID across renames/moves)."""
        return self.history().for_node(node_id)

    # -- export / import / merge ----------------------------------------

    def export_bundle(self, *, node_ids: Sequence[str] | None = None,
                      revision: str | None = None) -> GraphBundle:
        """The whole graph, or the containment subtrees of ``node_ids``."""
        return self.owner.export_bundle(self.graph_id, revision=revision, node_ids=node_ids)

    def bootstrap_from(self, bundle: GraphBundle | bytes, *, reason: str = "seed from bundle") -> dict[str, Any]:
        """Seed this (empty) graph from a whole-graph bundle, under this graph_id."""
        bundle = bundle if isinstance(bundle, GraphBundle) else GraphBundle.from_json(bundle)
        return self.bootstrap(bootstrap_definition(bundle, graph_id=self.graph_id), reason=reason)

    def import_bundle(self, bundle: GraphBundle | bytes, *, target: Mapping[str, object], reason: str,
                      identity: str = "preserve", remap: Mapping[str, str] | None = None,
                      prefix: str | None = None, external: Mapping[str, str] | None = None,
                      preview: bool = False) -> Any:
        """Import subtrees as one ordinary transaction (preview only if requested).

        ``identity="preserve"`` (default) refuses any ID already live or retained
        in this graph; ``"remap"`` renames via ``remap``/``prefix``. ``target`` is
        ``{"parent_id": ...}`` or ``{"plane": "model"|"prompt"}``.
        """
        bundle = bundle if isinstance(bundle, GraphBundle) else GraphBundle.from_json(bundle)
        envelope = self.owner.import_envelope(self.graph_id, bundle, target=target,
            idempotency_key=str(uuid4()), reason=reason, correlation_id=str(uuid4()),
            identity=identity, remap=remap, prefix=prefix, external=external)
        proposal = self.owner.preview(envelope, expires_at=self._expires())
        if preview:
            return proposal
        receipt = cast(dict[str, Any], self.owner.commit(proposal))
        self._snapshot()
        return receipt

    def merge(self, theirs: GraphBundle | GraphDefinition | bytes, *, reason: str,
              base_revision: str | None = None, resolutions: Mapping[str, Any] | None = None,
              preview: bool = False) -> tuple[MergeResult, Any]:
        """Three-way merge ``theirs`` into the head (base: the bundle's source revision).

        Returns ``(result, None)`` with conflicts when unresolved, otherwise
        ``(result, receipt)`` after an ordinary admitted commit, or
        ``(result, preview)`` when ``preview=True``.
        """
        if isinstance(theirs, bytes):
            theirs = GraphBundle.from_json(theirs)
        result, envelope = self.owner.merge_envelope(self.graph_id, theirs, base_revision=base_revision,
            resolutions=resolutions, idempotency_key=str(uuid4()), reason=reason,
            correlation_id=str(uuid4()))
        if envelope is None:
            return result, None
        proposal = self.owner.preview(envelope, expires_at=self._expires())
        if preview:
            return result, proposal
        receipt = self.owner.commit(proposal)
        self._snapshot()
        return result, receipt

    def export_history(self) -> HistoryBundle:
        return self.owner.export_history(self.graph_id)

    def import_history(self, bundle: HistoryBundle | bytes, *, approval_ref: str = "import") -> dict[str, Any]:
        """Recreate this graph with its full history (the graph must not exist)."""
        bundle = bundle if isinstance(bundle, HistoryBundle) else HistoryBundle.from_json(bundle)
        if bundle.graph_id != self.graph_id:
            raise GraphError("schema_invalid", "History bundle belongs to another graph")
        receipt = cast(dict[str, Any], self.owner.import_history(bundle, approval_ref=approval_ref,
                                                                 correlation_id=str(uuid4())))
        if self._releases is None:
            self._start_serving()
        self._snapshot(force=True)
        return receipt

    # -- serving ----------------------------------------------------------

    async def publish(self, *, timeout: float = 30, policy: str = "latest",
                      reason: str = "publish head") -> ReleaseWaitResult:
        """Release the accepted head and wait until this instance serves it.

        Preparation happens outside the owner lock; in-flight requests keep
        their pinned generation and the old one retires when they drain.
        """
        snapshot = self.read()
        plan = self._provider.compile(snapshot.definition)
        desired = self.releases.status().desired_epoch
        request = {"schema": "contexture.release-request/v1", "graph_id": self.graph_id,
            "target_id": self.target_id, "revision": snapshot.revision,
            "definition_digest": snapshot.definition_digest,
            "manifest_digest": plan.manifest.manifest_digest,
            "expected_desired_epoch": desired or "0", "idempotency_key": str(uuid4()),
            "policy": policy, "reason": reason, "approval_ref": "release"}
        receipt: ReleaseReceipt = self.releases.request(request)
        result = await self.releases.wait(receipt, timeout=timeout)
        _LOG.info("release published", extra={"graph_id": self.graph_id,
                  "revision": snapshot.revision, "status": result.status})
        if result.status != "observed":
            status = self.releases.status()
            raise GraphError(status.last_error or "not_ready",
                             f"Release did not become active: {result.status}")
        return result

    async def wait_ready(self, *, timeout: float = 30) -> None:
        """Wait until this instance serves the durable desired release.

        Used after reopening a store: serving resumes the desired release in the
        background. Raises with the release manager's error code if it refuses.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            status = self.releases.status()
            if status.readiness == "ready":
                return
            if (status.running_epoch is None and status.pending_epoch is None
                    and status.last_attempt is not None and status.last_attempt[1] != "local_observed"):
                raise GraphError(status.last_error or "not_ready",
                                 f"Desired release not served: {status.last_attempt[1]}")
            if loop.time() >= deadline:
                raise GraphError("not_ready", "Timed out waiting for the desired release")
            await asyncio.sleep(0.01)

    # -- helpers ----------------------------------------------------------

    def _snapshot(self, *, force: bool = False) -> None:
        pending = self.owner.records_since_snapshot
        if not self._snapshot_every or pending == 0 or self.owner.writes_stopped:
            return
        if force or pending >= self._snapshot_every:
            try:
                self.owner.checkpoint()
            except GraphError as error:  # acceleration only; never fail the caller
                _LOG.warning("owner snapshot skipped", extra={"error_code": error.code})

    def _expires(self) -> str:
        return _later(self._clock(), self._preview_seconds)

    def _envelope(self, commands: Sequence[Mapping[str, Any]], reason: str,
                  idempotency_key: str | None) -> dict[str, Any]:
        return {"schema": "contexture.transaction/v1", "graph_id": self.graph_id,
                "base_revision": self.read().revision,
                "idempotency_key": idempotency_key or str(uuid4()),
                "commands": [dict(c) for c in commands], "reason": reason,
                "correlation_id": str(uuid4())}
