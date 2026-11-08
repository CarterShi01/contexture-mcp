"""Bounded WP08/09 local request execution, not a Host or wire adapter.

Only trusted deployment code supplies identity, authority and adapter ports.
No Principal wire shape, payload credentials, implicit session, persistent pin,
implicit run token, factory inference or legacy-path fallback is provided. Requests are
single-task contexts: exit releases their resources, and escaped calls refuse.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, cast

from contexture.application.managed_run_leases import RunLeaseContext
from contexture.application.managed_serving import GenerationLease, LocalServingCoordinator
from contexture.core.graph.bindings import ResolvedBinding, _schema, _validate
from contexture.core.graph.canonical import FrozenJSON, freeze_json, thaw_json, validate_id
from contexture.core.graph.compiler import StructuralPlan
from contexture.core.graph.disclosure import ManagedDisclosure
from contexture.core.graph.errors import GraphError
from contexture.core.graph.index import GraphIndex
from contexture.core.graph.model import PromptPublication, ResourcePublication, RoutePublication
from contexture.core.graph.policy import AdmissionDecision, VerifiedContext
from contexture.core.graph.references import ManagedReference, format_reference, parse_reference


def _deny(message: str, code: str = "permission_denied") -> GraphError:
    return GraphError(code, message)


class CurrentRequestContext(Protocol):
    def current(self) -> VerifiedContext:
        """Return current authenticated Host evidence, never business arguments."""
        ...


@dataclass(frozen=True, slots=True)
class RequestAuthorization:
    context: VerifiedContext
    current_index: GraphIndex
    operation: str
    requested_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProjectionSnapshot:
    """Trusted local projection input, never an unfiltered wire response.

    The complete immutable plan is custody, not visibility. Consumers MUST
    filter by authorized_ids and retain their explicit request lifetime;
    this value grants no permission and cannot execute or renew a lease.
    """

    plan: StructuralPlan
    revision: str
    generation_uuid: str
    context: VerifiedContext
    authorized_ids: frozenset[str]


class RequestAuthority(Protocol):
    async def verify(self, request: RequestAuthorization) -> AdmissionDecision:
        """Attest CURRENT policy for stable IDs in the supplied active index.

        Fail closed if policy cannot verify those IDs. A successful decision
        attests this exact index and context at its return boundary, not just
        resource ownership or permission at some earlier await. Deployments must
        fence external policy changes as appropriate; this local single-loop
        implementation also detects active-pointer and context changes on return.
        """
        ...


class ExecutionAdapter(Protocol):
    async def call(self, binding: ResolvedBinding, arguments: FrozenJSON,
                   context: VerifiedContext, resource: object) -> object:
        """Use exactly this captured binding; own native validation and execution.

        Schema preflight and raw-JSON validation precede this call. No additional
        await is allowed before synchronous decoder/validator/handler dispatch;
        awaiting the business handler is allowed. Generic adapters are trusted
        code and must document their own ABI and result/mutability contract.
        """
        ...


@dataclass(frozen=True, slots=True)
class ExecutionAdapterRegistration:
    """Local support attestation for one exact binding contract, never authored JSON.

    node_id=None attests the binding for ANY node bound to exactly this
    binding_id/version/abi/contract_digest, so a Tool node created at runtime
    against an already deployed binding executes without rebuilding the
    runtime. A node-keyed registration takes precedence for its node.
    """

    node_id: str | None
    binding_id: str
    version: str
    abi: str
    contract_digest: str
    adapter: ExecutionAdapter = field(repr=False, compare=False)

    def verify(self, node_id: str, binding: ResolvedBinding) -> None:
        entry, registration = binding.entry, binding.registration
        if ((self.node_id if self.node_id is not None else node_id, self.binding_id,
                self.version, self.abi, self.contract_digest) !=
                (node_id, entry.binding.binding_id, entry.binding.version,
                 entry.abi, entry.contract_digest)
                or (registration.binding_id, registration.version, registration.abi,
                    registration.contract_digest, registration.effect,
                    registration.artifact_digest, registration.validator_contract_id) !=
                (entry.binding.binding_id, entry.binding.version, entry.abi,
                 entry.contract_digest, entry.effect, entry.artifact_digest,
                 entry.validator_contract_id)
                or freeze_json(entry.input_schema) != registration.input_schema):
            raise _deny("Execution adapter does not match pinned contract", "binding_contract_invalid")
        if isinstance(self.adapter, PythonManagedCallAdapter) and self.abi != "python-managed-call/v1":
            raise _deny("Unsupported explicit Python ABI", "binding_contract_invalid")
        _schema(registration.input_schema)  # all schemas before any execution


class PythonManagedCallAdapter:
    """Explicit LOCAL python-managed-call/v1 ABI, not a portable contract.

    input_schema describes raw JSON (including accepted coercible values).
    decoder(detached arguments) synchronously applies registered defaults and
    coercions; validator(decoded) synchronously validates the COMPLETE native
    contract and returns None. handler(decoded, verified context, resource)
    returns an Awaitable. No signature inspection/rebinding or factory calls.
    All three callables come from the pinned registration, never a live registry.
    This ABI promises JSON output: copy it, without claiming business state is
    deeply immutable. Non-JSON native results need a different explicit adapter.
    """

    async def call(self, binding: ResolvedBinding, arguments: FrozenJSON,
                   context: VerifiedContext, resource: object) -> object:
        registration = binding.registration
        if registration.abi != "python-managed-call/v1":
            raise _deny("Unsupported explicit Python ABI", "binding_contract_invalid")
        decoded = registration.decoder(thaw_json(arguments))
        if registration.validator(decoded) is not None:
            raise _deny("Native validator must return None", "binding_contract_invalid")
        handler = cast(Callable[[object, VerifiedContext, object], Awaitable[object]],
                       registration.handler)
        return thaw_json(await handler(decoded, context, resource))


class ManagedRuntime:
    """Transport-neutral model-door runtime with explicit trusted local ports.

    selection is a set of stable subtree anchors, expanded ONCE in the pinned
    graph. Initial authorization intersects it; subsequent checks only attenuate
    that ceiling. Identity and graph scope are not read from arguments. No
    user-controlled authority or selection is accepted by publication doors.
    publication_audience is an explicit trusted Host switch: it admits prompt
    plane IDs into the initial ceiling without changing model navigation.
    """

    def __init__(self, coordinator: LocalServingCoordinator, *,
                 contexts: CurrentRequestContext, authority: RequestAuthority,
                 adapters: tuple[ExecutionAdapterRegistration, ...]) -> None:
        self._coordinator, self._contexts, self._authority = coordinator, contexts, authority
        captured: dict[tuple[str | None, str, str, str, str], ExecutionAdapterRegistration] = {}
        for registration in adapters:
            if not isinstance(registration, ExecutionAdapterRegistration):
                raise _deny("Trusted execution registration required", "binding_contract_invalid")
            if registration.node_id is not None:
                validate_id(registration.node_id)
            key = (registration.node_id, registration.binding_id, registration.version,
                   registration.abi, registration.contract_digest)
            if key in captured:
                raise _deny("Duplicate execution registration", "binding_contract_invalid")
            captured[key] = registration
        self._adapters = MappingProxyType(captured)

    def _context(self) -> VerifiedContext:
        context = self._contexts.current()
        if not isinstance(context, VerifiedContext):
            raise _deny("Verified current request context required")
        return context

    def _preflight(self, lease: GenerationLease) -> Mapping[str, ExecutionAdapterRegistration]:
        plan = lease.generation.plan
        if set(lease.handles) != set(plan.bindings):
            raise _deny("Generation resource handles differ", "binding_contract_invalid")
        result: dict[str, ExecutionAdapterRegistration] = {}
        for node_id, binding in plan.bindings.items():
            entry = binding.entry
            key = (node_id, entry.binding.binding_id, entry.binding.version,
                   entry.abi, entry.contract_digest)
            registration = self._adapters.get(key) or self._adapters.get((None, *key[1:]))
            if registration is None:
                raise _deny("No exact execution adapter", "binding_contract_invalid")
            registration.verify(node_id, binding)
            result[node_id] = registration
        return MappingProxyType(result)

    async def _authorize(self, identity: VerifiedContext, operation: str,
                         requested: frozenset[str], *, visibility_only: bool = False,
                         publication_audience: bool = False,
                         ) -> tuple[VerifiedContext, frozenset[str]]:
        # A concurrent local swap during external policy await invalidates that
        # graph evidence. Retry boundedly with the new active graph, never A's
        # historical graph as a substitute for current authority.
        for _ in range(3):
            context = self._context()
            if (context.actor_id, context.tenant_id) != (identity.actor_id, identity.tenant_id):
                raise _deny("Request actor or tenant changed")
            async with self._coordinator.request_lease() as current:
                index = current.generation.plan.index
                if index.definition.graph_id != self._coordinator.graph_id:
                    raise _deny("Current graph cannot be verified")
                decision = await self._authority.verify(
                    RequestAuthorization(context, index, operation, requested))
                if (not isinstance(decision, AdmissionDecision) or not decision.allowed
                        or decision.approval_required
                        or decision.authority_revision != context.authority_revision):
                    raise _deny("Current request authority denied or unverifiable")
                if self._context() != context:
                    raise _deny("Authority changed during decision")
                async with self._coordinator.request_lease() as latest:
                    if latest.generation is not current.generation:
                        continue
                    # Intersect against actual CURRENT stable IDs and doors,
                    # even if a buggy port returned an unknown/deleted ID.
                    visible = frozenset(n for n in decision.scope_ids
                        if n in index.locations and (publication_audience or
                            (index.locations[n].plane == "model"
                            if visibility_only else index.model_may_open(n))))
                    return context, requested & visible
        raise _deny("Current active graph changed during authorization")

    def _selection(self, lease: GenerationLease,
                   selection: frozenset[str] | None, *, visibility_only: bool = False,
                   publication_audience: bool = False,
                   ) -> frozenset[str]:
        plan = lease.generation.plan
        if plan.definition.graph_id != self._coordinator.graph_id:
            raise _deny("Pinned graph cannot be verified")
        if selection is None:
            selected = frozenset(plan.index.preorder)
        else:
            anchors = frozenset(validate_id(n) for n in selection)
            if not anchors <= plan.index.locations.keys():
                raise _deny("Selection is unavailable")
            selected = frozenset(node.node_id for n in anchors for node in plan.index.subtree(n))
        return frozenset(n for n in selected if (publication_audience or
            (plan.index.locations[n].plane == "model"
            if visibility_only else plan.index.model_may_open(n))))

    @asynccontextmanager
    async def request(self, *, selection: frozenset[str] | None = None,
                      run_context: RunLeaseContext | None = None,
                      publication_audience: bool = False,
                      ) -> AsyncIterator[ManagedRequest]:
        identity = self._context()
        if run_context is not None and type(run_context) is not RunLeaseContext:
            raise _deny("Trusted run context required")
        manager = (self._coordinator.request_lease() if run_context is None else
                   run_context._service._request(run_context, self))
        # Enter BEFORE the first external authorization await. Creating the
        # async manager alone intentionally does not capture an active pointer.
        async with manager as lease:
            selected = self._selection(lease, selection, visibility_only=True,
                                       publication_audience=publication_audience)
            if run_context is not None:
                selected &= run_context._service._validate(run_context).ceiling
            adapters = self._preflight(lease)
            _, ceiling = await self._authorize(identity, "request", selected, visibility_only=True,
                                               publication_audience=publication_audience)
            if run_context is not None:
                run_context._service._validate(run_context)
            request = ManagedRequest(self, lease, identity, ceiling, adapters, run_context)
            try:
                yield request
            finally:
                request._closed = True


class ManagedRequest:
    """Single-task request handle. Not a reusable run token or permission proof."""

    def __init__(self, runtime: ManagedRuntime, lease: GenerationLease,
                 identity: VerifiedContext, ceiling: frozenset[str],
                 adapters: Mapping[str, ExecutionAdapterRegistration],
                 run_context: RunLeaseContext | None = None) -> None:
        self._runtime, self._lease, self._identity = runtime, lease, identity
        self._ceiling, self._adapters = ceiling, adapters
        self._run_context = run_context
        self._task, self._closed = asyncio.current_task(), False

    @property
    def generation_uuid(self) -> str:
        return self._lease.generation.context.generation_uuid

    def reference(self, node_id: str) -> ManagedReference:
        """Generation-qualified reference to a node in THIS request's pinned graph."""
        self._check_navigation()
        return ManagedReference(self._lease.generation.plan.definition.graph_id,
                                self.generation_uuid, validate_id(node_id))

    @property
    def is_run_pinned(self) -> bool:
        """Explicit lifetime routing, not identity or an authorization decision."""
        self._check_navigation()
        return self._run_context is not None

    def _check_navigation(self) -> None:
        if self._closed or asyncio.current_task() is not self._task:
            raise _deny("Request is closed or belongs to another task")
        if self._run_context is not None:
            self._run_context._service._validate(self._run_context)
        if self._lease.release.boot_id != self._runtime._coordinator.boot_id:
            raise _deny("Reference does not match this request generation", "stale_reference")
        if self._lease.generation.plan.definition.graph_id != self._runtime._coordinator.graph_id:
            raise _deny("Pinned graph cannot be verified")

    async def _disclosure(self, operation: str) -> ManagedDisclosure:
        self._check_navigation()
        context, current = await self._runtime._authorize(
            self._identity, operation, self._ceiling,
            visibility_only=operation in ("discover", "route"))
        self._check_navigation()
        # Fresh evidence for this action only. Rendering is synchronous, without
        # another scheduling boundary or caching a view across policy changes.
        return ManagedDisclosure(plan=self._lease.generation.plan,
            generation_uuid=self.generation_uuid, authorized_ids=current,
            current_view_evidence=context.authority_revision)

    async def discover(self) -> dict[str, Any]:
        return (await self._disclosure("discover")).discover()

    async def projection_snapshot(self) -> ProjectionSnapshot:
        """Capture this request's exact plan with fresh CURRENT model scope."""
        self._check_navigation()
        context, current = await self._runtime._authorize(
            self._identity, "projection", self._ceiling, visibility_only=True)
        self._check_navigation()
        generation = self._lease.generation
        return ProjectionSnapshot(generation.plan, generation.context.revision,
                                  self.generation_uuid, context, current)

    async def route(self, reference: str | ManagedReference) -> dict[str, Any]:
        return (await self._disclosure("route")).route(str(reference)
            if isinstance(reference, ManagedReference) else reference)

    async def inspect(self, references: Sequence[str]) -> dict[str, Any]:
        self._check_navigation()
        if isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
            raise _deny("Inspect requires an ordered reference sequence", "schema_invalid")
        if not 1 <= len(references) <= 32:
            raise _deny("Inspect reference count is outside its limit", "schema_invalid")
        captured = tuple(references)
        return (await self._disclosure("inspect")).inspect(captured)

    async def open(self, reference: str | ManagedReference) -> dict[str, Any]:
        return (await self._disclosure("open")).open(str(reference)
            if isinstance(reference, ManagedReference) else reference)

    async def get_prompt(self, publication_id: str | None = None) -> dict[str, Any]:
        """Trusted publication door; None lists authorized explicit publications.

        No arguments or path/ref fallback. The person audience changes structural
        disclosure only, never current authorization or the selected ceiling.
        This is a local candidate API, not an accepted portable wire contract.
        """
        self._check_navigation()
        context, current = await self._runtime._authorize(
            self._identity, "get_prompt", self._ceiling, publication_audience=True)
        self._check_navigation()
        plan = self._lease.generation.plan
        publications = {p.publication_id: p for p in plan.definition.publications
                        if isinstance(p, PromptPublication) and p.opens_id in current}
        if publication_id is None:
            return {key: dict(p.as_json(), ref=format_reference(
                plan.definition.graph_id, self.generation_uuid, p.opens_id))
                for key, p in publications.items()}
        publication = publications.get(publication_id)
        if publication is None:
            raise _deny("Publication is unavailable")
        disclosure = ManagedDisclosure(plan=plan, generation_uuid=self.generation_uuid,
            authorized_ids=current, current_view_evidence=context.authority_revision,
            audience="person")
        return disclosure.open(format_reference(plan.definition.graph_id,
                                                self.generation_uuid, publication.opens_id))

    async def read_resource(self, publication_id: str | None = None, *,
                            expected_uri: str | None = None) -> dict[str, Any]:
        """Trusted explicit Resource door; None lists, otherwise invokes read-only.

        The optional exact URI fences a mapping selected earlier in THIS request.
        There is no path lookup, URI normalization, or historical URI alias.
        """
        self._check_navigation()
        _, current = await self._runtime._authorize(
            self._identity, "read_resource", self._ceiling, publication_audience=True)
        self._check_navigation()
        plan = self._lease.generation.plan
        publications = {p.publication_id: p for p in plan.definition.publications
                        if isinstance(p, ResourcePublication) and p.opens_id in current}
        if publication_id is None:
            return {key: dict(p.as_json(), ref=format_reference(
                plan.definition.graph_id, self.generation_uuid, p.opens_id))
                for key, p in publications.items()}
        publication = publications.get(publication_id)
        if publication is None or (expected_uri is not None and publication.uri != expected_uri):
            raise _deny("Publication is unavailable")
        contract = plan.publication_contracts[publication.opens_id]
        if contract.effect != "read_only" or not contract.argument_free:
            raise _deny("Resource contract is invalid", "binding_contract_invalid")
        result = await self._invoke(format_reference(plan.definition.graph_id,
            self.generation_uuid, publication.opens_id), {}, "read_only", publication_audience=True)
        return {"value": result}

    async def list_routes(self) -> tuple[RoutePublication, ...]:
        """Trusted Host metadata from THIS pin, filtered by CURRENT authority.

        Local candidate API, not public graph discovery or a portable wire
        profile. Enter request(publication_audience=True) for publication doors.
        """
        self._check_navigation()
        _, current = await self._runtime._authorize(
            self._identity, "list_routes", self._ceiling, publication_audience=True)
        self._check_navigation()
        return tuple(p for p in self._lease.generation.plan.definition.publications
                     if isinstance(p, RoutePublication) and p.opens_id in current)

    async def invoke_route(self, publication_id: str, arguments: object = None, *,
                           expected_method: str, expected_path: str) -> object:
        """Invoke an exact explicit Route in this pin; never resolve a live alias.

        Method/path are mandatory fences supplied by the trusted HTTP adapter.
        The compiled GraphIndex attests the method/effect pairing; execution
        rechecks the pinned contract and current authority through _invoke.
        """
        self._check_navigation()
        plan = self._lease.generation.plan
        publication = next((p for p in plan.definition.publications
            if isinstance(p, RoutePublication) and p.publication_id == publication_id), None)
        if (publication is None or publication.method != expected_method
                or publication.path != expected_path):
            raise _deny("Publication is unavailable")
        effect = "read_only" if publication.method in ("GET", "HEAD") else "write"
        if plan.publication_contracts[publication.opens_id].effect != effect:
            raise _deny("Route contract is invalid", "binding_contract_invalid")
        return await self._invoke(format_reference(plan.definition.graph_id,
            self.generation_uuid, publication.opens_id), arguments, effect,
            publication_audience=True)

    async def invoke_read_only(self, reference: str | ManagedReference,
                               arguments: object = None) -> object:
        return await self._invoke(reference, arguments, "read_only")

    async def invoke(self, reference: str | ManagedReference, arguments: object = None) -> object:
        return await self._invoke(reference, arguments, "write")

    async def _invoke(self, reference: str | ManagedReference, arguments: object,
                      effect: str, *, publication_audience: bool = False) -> object:
        if self._closed or asyncio.current_task() is not self._task:
            raise _deny("Request is closed or belongs to another task")
        if self._run_context is not None:
            self._run_context._service._validate(self._run_context)
        ref = parse_reference(str(reference)) if isinstance(reference, ManagedReference) else parse_reference(reference)
        lease = self._lease
        plan = lease.generation.plan
        if ((ref.graph_id, ref.generation_uuid) !=
                (plan.definition.graph_id, self.generation_uuid)
                or lease.release.boot_id != self._runtime._coordinator.boot_id
                or ref.node_id not in plan.index.locations):
            raise _deny("Reference does not match this request generation", "stale_reference")
        node_id = ref.node_id
        if node_id not in self._ceiling or (not publication_audience and not plan.index.model_may_open(node_id)):
            raise _deny("Node outside request scope or model door")
        binding = plan.bindings.get(node_id)
        if binding is None:
            raise _deny("Reference is not an executable Tool")
        if binding.entry.effect != effect:
            raise _deny("Binding effect requires the other execution door", "wrong_door")
        # freeze_json recursively copies, rejects non-JSON and removes live
        # aliases before an async policy decision can give callers control.
        raw = freeze_json({} if arguments is None else arguments)
        if not isinstance(raw, Mapping):
            raise _deny("Tool arguments must be a JSON object", "schema_invalid")
        _validate(binding.registration.input_schema, raw)
        context, current = await self._runtime._authorize(
            self._identity, "invoke_read_only" if effect == "read_only" else "invoke", self._ceiling,
            publication_audience=publication_audience)
        if self._run_context is not None:
            self._run_context._service._validate(self._run_context)
        if node_id not in current:
            raise _deny("Current authority no longer permits the target")
        # No scheduling boundary before the explicitly trusted adapter enters
        # its synchronous validation/handler-dispatch prefix.
        return await self._adapters[node_id].adapter.call(binding, raw, context, lease.handles[node_id])