"""Bounded WP08 durable intent integration, not an asynchronous ReleaseManager.

Register release_projection() BEFORE GraphOwner construction/replay. New intents
use one owner serialization/fence; no compiler, preparation, handler, swap, lease
or active-generation reconstruction is performed here. Full replay stays pure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from contexture.application.managed import EventProjection, GraphOwner
from contexture.core.graph.bindings import ExecutionManifest
from contexture.core.graph.canonical import FrozenJSON, parse_counter, thaw_json, validate_id
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import RevisionState
from contexture.core.graph.policy import AdmissionRequest, VerifiedContext
from contexture.core.graph.releases import (
    RELEASE_EVENT_TYPES, ReleaseReceipt, ReleaseRequest, empty_release_state,
    import_release_state, reduce_release_event,
)

PROJECTION_NAME = "releases"


def release_projection() -> EventProjection:
    """Pure registered projection for all release tags, including old boots."""
    return EventProjection(RELEASE_EVENT_TYPES, empty_release_state(), reduce_release_event)


@dataclass(frozen=True, slots=True)
class VerifiedReleaseManifest:
    """Trusted adapter attestation, NOT author/model-supplied wire credentials.

    The manifest alone does not bind a definition: these exact journal-history
    coordinates attest the compilation/binding proof for that pinned snapshot.
    """

    graph_id: str
    revision: str
    definition_digest: str
    manifest: ExecutionManifest


class ReleaseManifestProvider(Protocol):
    def verify(self, request: ReleaseRequest, snapshot: RevisionState,
               context: VerifiedContext) -> VerifiedReleaseManifest:
        """Validate CURRENT trusted compile/registry evidence for this snapshot.

        Trusted synchronous local adapter, called under owner lock+authority fence.
        It may perform I/O, but must not await, reenter the owner, run handlers or
        prepare resources. It must validate bindings against this exact historical
        definition, target compatibility and current registry authority, not merely
        compare a caller manifest hash with head. This is NOT a swap gate. Replay
        never calls this port. A digest-only author assertion cannot implement it.
        """
        ...


class ReleaseIntentCoordinator:
    """Persist authorized intent+original receipt; never claim serving success.

    A later explicit request supersedes the previous desired intent for both
    policies. Exact never becomes silently skipped/successful; latest permits
    future preparation coalescing but this slice does not schedule attempts.
    """

    def __init__(self, owner: GraphOwner, *, manifests: ReleaseManifestProvider):
        self.owner, self.manifests = owner, manifests

    def request(self, value: object) -> ReleaseReceipt:
        with self.owner.release_boundary() as boundary:
            request = ReleaseRequest.from_json(value)
            digest = request.digest()
            prior = boundary.prior_receipt(request.graph_id, request.idempotency_key, digest)
            if prior is not None:
                result = ReleaseReceipt.from_json(prior["operation_result"]["inline"])
                identity = cast(Mapping[str, object], result.as_json()["release"])
                if (identity["graph_id"] != request.graph_id or identity["target_id"] != request.target_id
                        or result.as_json()["request_digest"] != digest):
                    raise GraphError("idempotency_conflict", "Key belongs to another operation")
                return result

            # Registration and reducer shape must exist before any durable write.
            state = import_release_state(boundary.projection(PROJECTION_NAME))
            desired = cast(tuple[Mapping[str, FrozenJSON], ...], state["desired_releases"])
            previous = next((item for item in desired
                if cast(Mapping[str, FrozenJSON], item["request"])["graph_id"] == request.graph_id
                and cast(Mapping[str, FrozenJSON], item["request"])["target_id"] == request.target_id), None)
            expected = "0" if previous is None else str(previous["release_epoch"])
            if request.expected_desired_epoch != expected:
                raise GraphError("revision_conflict", "Desired release epoch changed")
            epoch = str(parse_counter(expected) + 1)
            parse_counter(epoch)
            snapshot = boundary.snapshot(request.graph_id, request.revision)
            if snapshot.definition_digest != request.definition_digest:
                raise GraphError("contract_mismatch", "Historical definition digest differs")
            proof = boundary.verify_approval(request.approval_ref, digest)
            admission = AdmissionRequest(boundary.context, request.graph_id, "release.request",
                ("release.request",), snapshot.revision, snapshot.definition_digest,
                request.manifest_digest, tuple(sorted(snapshot.definition.nodes)), (), digest, proof)
            boundary.admit(admission)
            evidence = self.manifests.verify(request, snapshot, boundary.context)
            if (not isinstance(evidence, VerifiedReleaseManifest)
                    or (evidence.graph_id, evidence.revision, evidence.definition_digest) !=
                    (request.graph_id, snapshot.revision, snapshot.definition_digest)
                    or not isinstance(evidence.manifest, ExecutionManifest)):
                raise GraphError("contract_mismatch", "Pinned compile evidence differs")
            manifest = ExecutionManifest.from_json(evidence.manifest.as_json())
            if manifest.manifest_digest != request.manifest_digest:
                raise GraphError("contract_mismatch", "Trusted manifest digest differs")
            # Provider I/O must not let an approval expire before append.
            proof = boundary.verify_approval(request.approval_ref, digest)
            admission = AdmissionRequest(boundary.context, request.graph_id, "release.request",
                ("release.request",), snapshot.revision, snapshot.definition_digest,
                request.manifest_digest, tuple(sorted(snapshot.definition.nodes)), (), digest, proof)
            sequence = boundary.next_sequence
            identity = {"graph_id": request.graph_id, "target_id": request.target_id, "release_epoch": epoch}
            result = ReleaseReceipt.from_json({"schema": "contexture.release-receipt/v1",
                "release": identity, "journal_seq": sequence, "request_digest": digest, "status": "requested"})
            events: list[Mapping[str, object]] = []
            if previous is not None:
                events.append({"type": "ReleaseSuperseded", "release": {
                    **identity, "release_epoch": previous["release_epoch"]}, "superseded_by": epoch})
            events.append({"type": "ReleaseRequested", "request": request.as_json(), "release_epoch": epoch})
            receipt = {"schema": "contexture.command-receipt/v1", "graph_id": request.graph_id,
                "actor_ref": boundary.actor_ref, "idempotency_key": request.idempotency_key,
                "request_digest": digest, "revision": snapshot.revision, "journal_seq": sequence,
                "status": "committed", "durability": boundary.durability,
                "operation_result": {"inline": result.as_json()}}
            boundary.append(events, request=admission, correlation_id=request.idempotency_key,
                            command_receipt=receipt, read_current=False,
                            projection_checks={PROJECTION_NAME: import_release_state})
            return result

    def status(self, graph_id: str, target_id: str) -> dict[str, object]:
        """Current authorized graph/target projection, not active/served state."""
        validate_id(target_id)
        with self.owner.release_boundary(write=False) as boundary:
            boundary.authorize_read(graph_id)
            state = import_release_state(boundary.projection(PROJECTION_NAME))
            out = cast(dict[str, object], thaw_json(state))
            for section, raw in out.items():
                items = cast(list[dict[str, object]], raw)
                def matches(item: dict[str, object]) -> bool:
                    if section == "desired_releases":
                        identity = cast(Mapping[str, object], item["request"])
                    elif section == "prepare_attempt_history":
                        identity = cast(Mapping[str, object],
                            cast(Mapping[str, object], item["identity"])["release"])
                    else:
                        identity = cast(Mapping[str, object], item["release"])
                    return identity["graph_id"] == graph_id and identity["target_id"] == target_id
                out[section] = [item for item in items if matches(item)]
            return out