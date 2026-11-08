"""Local WP08 serving only: no run leases, request authorization or fleet claim.

One coordinator per owner/graph/target on one event loop is a deployment
obligation. Intent acceptance stays exclusively in ReleaseIntentCoordinator.
Trusted providers compile exact captured history with current registry evidence;
prepare is resource-only and must unwind partial acquisition on BaseException.
No owner boundary spans an await. Retirement is owned, bounded and observable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Literal, Protocol, cast
from uuid import UUID

from contexture.application.managed import GraphOwner, ReleaseBoundary
from contexture.application.managed_generation import ReadyGeneration, _settle
from contexture.application.managed_releases import (
    PROJECTION_NAME, ReleaseManifestProvider,
)
from contexture.core.graph.canonical import FrozenJSON, validate_id
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import RevisionState
from contexture.core.graph.policy import AdmissionRequest, VerifiedContext
from contexture.core.graph.releases import (
    DesiredRelease, ReleaseRequest, import_release_state,
)

_LOG = logging.getLogger("contexture.managed.serving")


def _uuid(value: str) -> str:
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise GraphError("schema_invalid", "Canonical UUID required") from None
    return value


@dataclass(frozen=True, slots=True)
class CapturedRelease:
    request: ReleaseRequest
    epoch: str
    snapshot: RevisionState
    context: VerifiedContext
    instance_id: str
    boot_id: str
    attempt_id: str
    generation_id: str

    @property
    def identity(self) -> dict[str, object]:
        return {"graph_id": self.request.graph_id, "target_id": self.request.target_id,
                "release_epoch": self.epoch}

    @property
    def attempt(self) -> dict[str, object]:
        return {"release": self.identity, "instance_id": self.instance_id,
                "boot_id": self.boot_id, "attempt_id": self.attempt_id}


class ServingPreparationProvider(ReleaseManifestProvider, Protocol):
    async def prepare(self, captured: CapturedRelease) -> ReadyGeneration:
        """Prepare exact history using supplied UUIDs outside the owner lock.

        verify() must independently compile this historical definition against
        CURRENT trusted registry authority, including on the ready recheck.
        A previously accepted static manifest digest is not that proof.
        """
        ...


@dataclass(frozen=True, slots=True)
class LocalServingResult:
    captured: CapturedRelease
    status: Literal["local_observed", "serving_unobserved", "superseded", "failed"]
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class GenerationLease:
    generation: ReadyGeneration
    handles: Mapping[str, object]
    release: CapturedRelease


class LocalServingCoordinator:
    """Bounded explicit attempts; refusal rather than an implicit pending queue.

    Limits include in-flight caller tasks and pending retirements separately.
    A stuck resource can refuse future preparations, never block existing request
    admission. shutdown waits for real cleanup; it has no forced-close timeout.
    Local results are trusted in-process diagnostics, not authorized wire views.
    """

    def __init__(self, owner: GraphOwner, *, graph_id: str, target_id: str,
                 instance_id: str, boot_id: str, provider: ServingPreparationProvider,
                 max_preparations: int = 2, max_retirements: int = 8):
        for value in (graph_id, target_id, instance_id):
            validate_id(value)
        _uuid(boot_id)
        if any(type(n) is not int or n < 1 for n in (max_preparations, max_retirements)):
            raise ValueError("Positive concurrency limits required")
        self.owner, self.provider = owner, provider
        with owner.release_boundary(write=False) as boundary:
            self._store_origin = boundary.store_origin
        self.graph_id, self.target_id = graph_id, target_id
        self.instance_id, self.boot_id = instance_id, boot_id
        self.max_preparations, self.max_retirements = max_preparations, max_retirements
        self._loop = asyncio.get_running_loop()
        self._gate = Lock()
        self._active: tuple[CapturedRelease, ReadyGeneration] | None = None
        self._preparing: dict[str, asyncio.Task[object]] = {}
        self._attempts: set[str] = set()
        self._generations: set[str] = set()
        self._retirements: set[asyncio.Task[None]] = set()
        self._errors: list[BaseException] = []
        self._pending_observation: CapturedRelease | None = None
        self._closed = False
        self._shutdown: asyncio.Task[None] | None = None

    def _check(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise GraphError("resource_busy", "Coordinator belongs to another event loop")

    @property
    def cleanup_errors(self) -> tuple[BaseException, ...]:
        self._check()
        return tuple(self._errors)

    @property
    def counts(self) -> dict[str, int]:
        self._check()
        return {"preparing": len(self._preparing), "retiring": len(self._retirements),
                "cleanup_errors": len(self._errors), "active": int(self._active is not None)}

    def _state(self, boundary: ReleaseBoundary) -> Mapping[str, FrozenJSON]:
        return import_release_state(boundary.projection(PROJECTION_NAME))

    def _desired(self, boundary: ReleaseBoundary) -> tuple[ReleaseRequest, str]:
        for item in cast(tuple[Mapping[str, FrozenJSON], ...], self._state(boundary)["desired_releases"]):
            desired = DesiredRelease.from_json(item).as_json()
            request = ReleaseRequest.from_json(desired["request"])
            if (request.graph_id, request.target_id) == (self.graph_id, self.target_id):
                return request, cast(str, desired["release_epoch"])
        raise GraphError("not_ready", "No accepted desired release")

    def _admission(self, boundary: ReleaseBoundary, captured: CapturedRelease,
                   operation: str) -> AdmissionRequest:
        request = captured.request
        snapshot = boundary.snapshot(self.graph_id, request.revision)
        if snapshot.definition_digest != request.definition_digest:
            raise GraphError("contract_mismatch", "Historical definition differs")
        digest = request.digest()
        proof = boundary.verify_approval(request.approval_ref, digest)
        admission = AdmissionRequest(boundary.context, self.graph_id, operation, (operation,),
            snapshot.revision, snapshot.definition_digest, request.manifest_digest,
            tuple(sorted(snapshot.definition.nodes)), (), digest, proof)
        boundary.admit(admission)
        return admission

    def _verify(self, boundary: ReleaseBoundary, captured: CapturedRelease,
                ready: ReadyGeneration | None = None) -> None:
        evidence = self.provider.verify(captured.request, captured.snapshot, boundary.context)
        request = captured.request
        if ((evidence.graph_id, evidence.revision, evidence.definition_digest) !=
                (self.graph_id, request.revision, request.definition_digest)
                or evidence.manifest.manifest_digest != request.manifest_digest):
            raise GraphError("contract_mismatch", "Current compile evidence differs")
        if ready is not None and (
                (ready.context.graph_id, ready.context.revision, ready.context.generation_uuid) !=
                (self.graph_id, request.revision, captured.generation_id)
                or ready.plan.definition_digest != request.definition_digest
                or ready.plan.manifest != evidence.manifest or ready.resource_state != "prepared"):
            raise GraphError("contract_mismatch", "Prepared generation coordinates differ")

    def _terminal(self, boundary: ReleaseBoundary, captured: CapturedRelease) -> bool:
        attempts = cast(tuple[Mapping[str, FrozenJSON], ...], self._state(boundary)["prepare_attempt_history"])
        return any(item["identity"] == captured.attempt for item in attempts)

    def _append(self, boundary: ReleaseBoundary, captured: CapturedRelease,
                event: Mapping[str, object], operation: str) -> None:
        admission = self._admission(boundary, captured, operation)
        boundary.append([event], request=admission, correlation_id=captured.attempt_id,
                        read_current=False, projection_checks={PROJECTION_NAME: import_release_state})

    def _retire(self, ready: ReadyGeneration) -> None:
        async def close() -> None:
            try:
                await ready.retire()
            except BaseException as exc:
                self._errors.append(exc)
        task = asyncio.create_task(close())
        self._retirements.add(task)
        task.add_done_callback(self._retirements.discard)

    async def prepare(self, *, attempt_id: str, generation_id: str) -> LocalServingResult:
        self._check()
        _uuid(attempt_id)
        _uuid(generation_id)
        # Reserve capacity synchronously, before any provider await/task queue.
        if (self._closed or self._pending_observation is not None
            or len(self._preparing) >= self.max_preparations
                or len(self._retirements) + len(self._preparing) >= self.max_retirements):
            raise GraphError("resource_busy", "Serving preparation capacity unavailable")
        if attempt_id in self._attempts or generation_id in self._generations:
            raise GraphError("identity_conflict", "Attempt or generation already used")
        task = asyncio.current_task()
        assert task is not None
        with self.owner.release_boundary() as boundary:
            request, epoch = self._desired(boundary)
            captured = CapturedRelease(request, epoch, boundary.snapshot(self.graph_id, request.revision),
                boundary.context, self.instance_id, self.boot_id, attempt_id, generation_id)
            if self._terminal(boundary, captured):
                raise GraphError("identity_conflict", "Attempt already terminal")
            for item in cast(tuple[Mapping[str, FrozenJSON], ...], self._state(boundary)["prepare_attempt_history"]):
                if item.get("generation_id") == generation_id:
                    raise GraphError("identity_conflict", "Generation already recorded")
            self._admission(boundary, captured, "release.prepare")
            self._verify(boundary, captured)
        self._preparing[attempt_id] = task
        self._attempts.add(attempt_id)
        self._generations.add(generation_id)
        ready: ReadyGeneration | None = None
        swapped = False
        try:
            ready = await self.provider.prepare(captured)
            if not isinstance(ready, ReadyGeneration):
                raise GraphError("contract_mismatch", "Provider must return ReadyGeneration")
            if self._closed or task.cancelling():
                raise asyncio.CancelledError
            with self.owner.release_boundary() as boundary:
                self._admission(boundary, captured, "release.prepare")
                self._verify(boundary, captured, ready)
                # Provider/authority callbacks have finished. Recheck under the
                # same owner serialization as durable desired acceptance.
                current, epoch = self._desired(boundary)
                superseded = epoch != captured.epoch or current != captured.request
                self._append(boundary, captured, {"type": "ReleasePreparationSucceeded",
                    "attempt": captured.attempt, "generation_id": generation_id,
                    "manifest_digest": request.manifest_digest}, "release.prepare")
                if superseded:
                    return LocalServingResult(captured, "superseded")
                # Another already-running prepare may have swapped but failed
                # observation. Do not lose that retry obligation on replacement.
                if self._pending_observation is not None:
                    raise GraphError("resource_busy", "Prior local observation requires recovery")
                self._admission(boundary, captured, "release.swap")
                boundary.check()  # in particular: write/commit_unknown, not only read readiness
                current, epoch = self._desired(boundary)
                if epoch != captured.epoch or current != captured.request:
                    return LocalServingResult(captured, "superseded")
                if self._closed or task.cancelling():
                    raise asyncio.CancelledError
                if ready.resource_state != "prepared":
                    raise GraphError("not_ready", "Candidate is no longer prepared")
                replacement = (captured, ready)
                # Pure short LOCAL gate: no calls, I/O, await, serialization or
                # user-defined cleanup. Owner fence already holds authority.
                with self._gate:
                    previous = self._active
                    self._active = replacement
                swapped = True
                self._pending_observation = captured
            _LOG.info("generation activated", extra={"graph_id": self.graph_id,
                      "release_epoch": captured.epoch, "generation_id": captured.generation_id})
            if previous is not None:
                self._retire(previous[1])
            return self.retry_observation()
        except BaseException as exc:
            if swapped:
                # Even an unusual trusted BaseException after publication must
                # not describe the real pointer as a failed prepare or retire it.
                return LocalServingResult(captured, "serving_unobserved", exc)
            # A terminal success may already be durable (or unknown): never
            # rewrite it as a failure. Recovery resolves uncertain append first.
            try:
                with self.owner.release_boundary() as boundary:
                    if not self._terminal(boundary, captured):
                        self._append(boundary, captured, {"type": "ReleasePreparationFailed",
                            "attempt": captured.attempt, "diagnostics": [{"code": "preparation_failed",
                                "location": "", "message": "Local preparation did not publish"}]},
                            "release.prepare")
            except BaseException as journal_error:
                if journal_error is not exc:
                    self._errors.append(journal_error)
            if isinstance(exc, asyncio.CancelledError):
                raise
            _LOG.warning("generation preparation failed; last known good kept",
                         extra={"graph_id": self.graph_id, "release_epoch": captured.epoch,
                                "error_code": getattr(exc, "code", type(exc).__name__)})
            return LocalServingResult(captured, "failed", exc)
        finally:
            if isinstance(ready, ReadyGeneration) and not swapped:
                self._retire(ready)
            del self._preparing[attempt_id]

    def retry_observation(self, *, recovered_owner: GraphOwner | None = None) -> LocalServingResult:
        """Retry after caller closes/reopens owner; real pointer is never replayed.

        Rebinding is only allowed with no preparation in flight. The caller must
        supply the recovered owner for the SAME verified logical store origin.
        Cloned backups preserving the origin are trusted as the same store;
        deployment must prevent divergent live writers. Old durable
        observations are deduplicated using the complete local identity.
        Pending observation backpressures replacements, not request leases.
        """
        self._check()
        if recovered_owner is not None:
            if self._preparing:
                raise GraphError("resource_busy", "Recovery requires settled preparations")
        candidate_owner = self.owner if recovered_owner is None else recovered_owner
        captured = self._pending_observation
        if captured is None:
            if self._active is None:
                raise GraphError("not_ready", "No local serving generation")
            if recovered_owner is not None:
                with candidate_owner.release_boundary() as boundary:
                    if boundary.store_origin != self._store_origin:
                        raise GraphError("contract_mismatch", "Recovered store origin differs")
                self.owner = candidate_owner
            return LocalServingResult(self._active[0], "local_observed")
        try:
            with candidate_owner.release_boundary() as boundary:
                if boundary.store_origin != self._store_origin:
                    raise GraphError("contract_mismatch", "Recovered store origin differs")
                self._admission(boundary, captured, "release.observe")
                state = self._state(boundary)
                operations = cast(tuple[Mapping[str, FrozenJSON], ...], state["release_operations"])
                attempts = cast(tuple[Mapping[str, FrozenJSON], ...], state["prepare_attempt_history"])
                if (not any(item["release"] == captured.identity
                            and item["request_digest"] == captured.request.digest()
                            and item["status"] == "requested" for item in operations)
                        or not any(item["identity"] == captured.attempt and item["status"] == "ready"
                            and item["generation_id"] == captured.generation_id for item in attempts)):
                    raise GraphError("contract_mismatch", "Recovered release provenance differs")
                observations = cast(tuple[Mapping[str, FrozenJSON], ...], state["observations_history"])
                exists = any(item["release"] == captured.identity
                    and item["instance_id"] == self.instance_id and item["boot_id"] == self.boot_id
                    and item["generation_id"] == captured.generation_id
                    and item["revision"] == captured.request.revision
                    and item["definition_digest"] == captured.request.definition_digest
                    and item["manifest_digest"] == captured.request.manifest_digest for item in observations)
                if not exists:
                    observation = {"release": captured.identity, "instance_id": self.instance_id,
                        "boot_id": self.boot_id, "generation_id": captured.generation_id,
                        "revision": captured.request.revision,
                        "definition_digest": captured.request.definition_digest,
                        "manifest_digest": captured.request.manifest_digest,
                        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
                    self._append(boundary, captured, {"type": "ReplicaObserved",
                        "observation": observation}, "release.observe")
            # Keep even a closed original owner until validation and the entire
            # observation operation succeed; failed candidates never rebind it.
            self.owner = candidate_owner
            self._pending_observation = None
            return LocalServingResult(captured, "local_observed")
        except Exception as exc:
            return LocalServingResult(captured, "serving_unobserved", exc)

    def request_lease(self) -> AbstractAsyncContextManager[GenerationLease]:
        """Capture and reserve at entry before auth awaits; NOT permission.

        Creating a manager does not pin a generation. Enter it before awaiting
        external current authorization, retaining that exact pin afterwards. No run
        token, persistent run pin or handler dispatch is supplied by this slice.
        """
        self._check()
        if self._closed or self._active is None:
            raise GraphError("not_ready", "No local serving generation")

        @asynccontextmanager
        async def lease() -> AsyncIterator[GenerationLease]:
            self._check()
            with self._gate:
                active = self._active
            if self._closed or active is None:
                raise GraphError("not_ready", "No local serving generation")
            captured, generation = active
            # ReadyGeneration.lease increments synchronously up to its first
            # yield on this same loop: capture-to-reservation cannot suspend.
            async with generation.lease() as handles:
                yield GenerationLease(generation, handles, captured)
        return lease()

    async def shutdown(self) -> None:
        """Stop admission, cancel preparations cooperatively, drain real leases."""
        self._check()
        if self._shutdown is None:
            self._closed = True
            with self._gate:
                active = self._active
                self._active = None
            if active is not None:
                self._retire(active[1])
            preparing = tuple(set(self._preparing.values()))
            for task in preparing:
                task.cancel()

            async def drain() -> None:
                await asyncio.gather(*preparing, return_exceptions=True)
                while self._retirements:
                    await asyncio.gather(*tuple(self._retirements))
            self._shutdown = asyncio.create_task(drain())
        await _settle(self._shutdown)