"""WP07 resource preparation, separate from pure GraphCompiler compilation.

This LOCAL Python adapter ABI is not a normative wire protocol. Trusted adapters
must return a fresh generation-exclusive async context manager; acquisition is
in __aenter__, which must unwind its own partial acquisition on BaseException.
Only resource acquisition is permitted: no handlers, decoders, business
validators, business writes or background consumers. Opaque registration.factory
is NEVER called here. There is deliberately no built-in factory ABI adapter.

Handles are opaque capabilities, not JSON. Only a lease exposes a read-only
mapping; neither the ready value nor its repr exports handles. Mapping immutability
does not freeze external resource state. Trusted adapters must not reuse owned
resources across generations or close Host-wide/shared resources. Sharing is not
implemented. These trust obligations cannot be proved for arbitrary Python code.

No serving pointer, release manager, admission, run authorization or active flag
is implemented. Lease counts protect resource lifetime, not current authority.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol, TypeVar
from uuid import UUID

from contexture.core.graph.bindings import ResolvedBinding
from contexture.core.graph.canonical import parse_counter, validate_id
from contexture.core.graph.compiler import StructuralPlan
from contexture.core.graph.errors import GraphError


def _invalid(message: str) -> GraphError:
    return GraphError("resource_preparation_invalid", message)


@dataclass(frozen=True, slots=True)
class PreparationContext:
    """Explicit provenance, not a generated UUID or proof of revision authority."""

    graph_id: str
    revision: str
    generation_uuid: str

    def __post_init__(self) -> None:
        validate_id(self.graph_id)
        parse_counter(self.revision)
        value = self.generation_uuid
        if type(value) is not str or len(value) != 36:
            raise _invalid("Expected a canonical generation UUID")
        try:
            if str(UUID(value)) != value:
                raise ValueError
        except ValueError:
            raise _invalid("Expected a canonical generation UUID") from None


class ResourcePreparationAdapter(Protocol):
    """Local Python port; prepare constructs a manager, async enter acquires.

    The binding/node/context are immutable pinned values. The adapter must not
    mutate callable closure state, invoke execution code, or infer a factory ABI.
    Exit is called once with (None, None, None), even on candidate failure: it is
    resource disposal, not business-exception suppression. Failed enter owns its
    partial cleanup; successfully entered managers are owned by this module.
    """

    def prepare(self, *, binding: ResolvedBinding, node_id: str,
                context: PreparationContext) -> AbstractAsyncContextManager[object]: ...


@dataclass(frozen=True, slots=True)
class PreparationAdapterRegistration:
    """Trusted explicit support attestation for one exact ABI AND contract.

    No dynamic supports callback runs during preflight. This attestation must
    come from deployment code, never author JSON. A factory bridge would need a
    separately implemented, explicitly supported ABI; this slice supplies none.
    """

    binding_id: str
    version: str
    abi: str
    contract_digest: str
    adapter: ResourcePreparationAdapter = field(repr=False, compare=False)

    def verify(self, binding: ResolvedBinding) -> None:
        entry, registration = binding.entry, binding.registration
        if (self.binding_id != entry.binding.binding_id
                or self.version != entry.binding.version or self.abi != entry.abi
                or self.contract_digest != entry.contract_digest
                or entry.abi != registration.abi
                or entry.contract_digest != registration.contract_digest):
            raise _invalid("Preparation adapter does not support the pinned ABI/contract")


T = TypeVar("T")


async def _settle(task: asyncio.Task[T]) -> T:
    """Join owned cleanup despite repeated caller cancellation; never detach it.

    Cancellation is re-raised AFTER real completion. Cleanup errors and caller
    cancellation are both retained rather than losing either to a finally block.
    No forced close/deadline is fabricated for an uncooperative resource.
    """
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
        except BaseException:
            break  # task.result below preserves the owned task's exact failure
    try:
        result = task.result()
    except BaseException as exc:
        if cancelled is not None:
            raise BaseExceptionGroup("Cleanup failed during cancellation", [cancelled, exc]) from None
        raise
    if cancelled is not None:
        raise cancelled
    return result


class _Resources:
    """Single-event-loop lifetime state, never a cross-generation resource pool."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.stack = AsyncExitStack()
        self.handles: Mapping[str, object] = MappingProxyType({})
        self.errors: list[BaseException] = []
        self.leases = 0
        self.idle = asyncio.Event()
        self.idle.set()
        self.state: Literal["prepared", "draining", "retired", "cleanup_failed"] = "prepared"
        self.retirement: asyncio.Task[None] | None = None

    def check_loop(self) -> None:
        if asyncio.get_running_loop() is not self.loop:
            raise _invalid("Generation belongs to a different event loop")

    async def exit_resource(self, manager: AbstractAsyncContextManager[object]) -> None:
        try:
            await manager.__aexit__(None, None, None)
        except BaseException as exc:
            # Never let suppression or one bad exit skip the remaining stack.
            self.errors.append(exc)

    def acquire(self) -> None:
        self.check_loop()
        self.leases += 1
        self.idle.clear()

    def release(self) -> None:
        self.check_loop()
        self.leases -= 1
        if self.leases == 0:
            self.idle.set()

    async def close(self) -> None:
        await self.idle.wait()
        await self.stack.aclose()
        self.handles = MappingProxyType({})
        self.state = "cleanup_failed" if self.errors else "retired"
        if self.errors:
            raise BaseExceptionGroup("Resource cleanup failed", self.errors)


class RetainedGeneration:
    """Local lifetime capability, issued only by ReadyGeneration.retain().

    Not authorization. Closing the keeper stops child admission synchronously;
    already entered children independently retain resources through cancellation.
    There is no public flag that opens arbitrary draining generations.
    """

    __slots__ = ("_resources", "_closed")

    _resources: _Resources
    _closed: bool

    def __init__(self) -> None:
        raise TypeError("Use ReadyGeneration.retain")

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Mapping[str, object]]:
        resources = self._resources
        resources.check_loop()
        if self._closed or resources.state not in ("prepared", "draining"):
            raise _invalid("Retention capability is closed")
        resources.acquire()
        try:
            yield resources.handles
        finally:
            resources.release()

    def close(self) -> None:
        self._resources.check_loop()
        if not self._closed:
            self._closed = True
            self._resources.release()


@dataclass(frozen=True, slots=True, init=False)
class ReadyGeneration:
    """Immutable prepared value; creation is exclusively through prepare_generation.

    The private lifetime holder is mutable on its owning loop. Retire atomically
    stops new leases and waits for existing leases, then disposes exactly its own
    stack. Repeated/concurrent retirement joins the same result (including errors).
    Do not await retirement from inside your own lease: it waits for that lease.
    """

    plan: StructuralPlan
    context: PreparationContext
    _resources: _Resources = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise TypeError("Use prepare_generation")

    @property
    def resource_state(self) -> str:
        self._resources.check_loop()
        return self._resources.state

    def retain(self) -> RetainedGeneration:
        """Explicit synchronous reservation while prepared, never after draining.

        Trusted callers may retain inside an actually entered serving lease;
        obtaining a context manager without entering it grants no capability.
        The caller must close the keeper, independently of its child requests.
        """
        resources = self._resources
        resources.check_loop()
        if resources.state != "prepared":
            raise _invalid("Generation is draining or retired")
        keeper = object.__new__(RetainedGeneration)
        keeper._resources, keeper._closed = resources, False
        resources.acquire()
        return keeper

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Mapping[str, object]]:
        resources = self._resources
        resources.check_loop()
        if resources.state != "prepared":
            raise _invalid("Generation is draining or retired")
        resources.acquire()
        try:
            yield resources.handles
        finally:
            resources.release()

    async def retire(self) -> None:
        resources = self._resources
        resources.check_loop()
        if resources.retirement is None:
            resources.state = "draining"
            resources.retirement = asyncio.create_task(resources.close())
        await _settle(resources.retirement)


async def prepare_generation(
    plan: StructuralPlan, *, graph_id: str, revision: str, generation_uuid: str,
    adapters: Mapping[str, PreparationAdapterRegistration],
) -> ReadyGeneration:
    """Preflight ALL pinned adapters before effects, then acquire in forest order.

    The plan must be the trusted GraphCompiler output, not a deserialized claim.
    Adapters are node-keyed; every resolved node requires an explicit attestation.
    Unbound disclosure tools do not request adapters or touch executable code.
    Revision is caller-attested canonical provenance, not checked journal history.
    """
    if not isinstance(plan, StructuralPlan):
        raise _invalid("Expected a compiled StructuralPlan")
    context = PreparationContext(graph_id, revision, generation_uuid)
    if context.graph_id != plan.definition.graph_id:
        raise _invalid("Graph identity does not match the structural plan")
    captured = dict(adapters)
    if set(captured) != set(plan.bindings):
        raise _invalid("Explicit adapters must match resolved binding nodes exactly")
    order = tuple(n for n in plan.index.preorder if n in plan.bindings)
    for node_id in order:
        adapter = captured[node_id]
        if not isinstance(adapter, PreparationAdapterRegistration):
            raise _invalid("Expected a trusted preparation adapter registration")
        adapter.verify(plan.bindings[node_id])
    resources = _Resources()
    handles: dict[str, object] = {}
    try:
        for node_id in order:
            manager = captured[node_id].adapter.prepare(
                binding=plan.bindings[node_id], node_id=node_id, context=context)
            handle = await manager.__aenter__()
            resources.stack.push_async_callback(resources.exit_resource, manager)
            handles[node_id] = handle
        resources.handles = MappingProxyType(dict(handles))
    except BaseException as original:
        resources.state = "draining"
        try:
            await _settle(asyncio.create_task(resources.close()))
        except asyncio.CancelledError as cleanup_cancelled:
            if isinstance(original, asyncio.CancelledError):
                raise original from None
            raise BaseExceptionGroup(
                "Preparation failed during cancelled cleanup", [original, cleanup_cancelled]) from None
        except BaseException as cleanup:
            raise BaseExceptionGroup("Preparation and cleanup failed", [original, cleanup]) from None
        raise
    ready = object.__new__(ReadyGeneration)
    object.__setattr__(ready, "plan", plan)
    object.__setattr__(ready, "context", context)
    object.__setattr__(ready, "_resources", resources)
    return ready