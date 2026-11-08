"""Durable intent through the real owner and strict native-temp FileJournal."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile

import pytest

from contexture.application.managed import EventProjection
from contexture.application.managed_releases import (
    PROJECTION_NAME, ReleaseIntentCoordinator, VerifiedReleaseManifest, release_projection,
)
from contexture.core.graph.bindings import AppDependencyContract, ExecutionManifest
from contexture.core.graph.canonical import canonical_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.policy import AdmissionRequest
from contexture.core.graph.releases import ReleaseRequest, reduce_release_event
from contexture.server.managed_journal import CommitUnknown, FileJournal
from tests.test_managed_owner import Host, LATER, NOW, boot, owner, request as graph_request

SCHEMA = json.loads((Path(__file__).parents[1] / "spec/managed/schemas/managed.schema.json").read_text())
MANIFEST = ExecutionManifest("1", "test-build", "sha256:" + "1" * 64, (),
    AppDependencyContract("app", "build"), ("local",))


class ReleaseHost(Host):
    def __init__(self):
        super().__init__()
        self.read_denied = set()
        self.release_denied = False
        self.bad_proof = None
        self.proofs = []

    def decide(self, request):
        decision = super().decide(request)
        if request.operation == "graph.read" and request.graph_id in self.read_denied:
            return replace(decision, allowed=False)
        if request.operation == "release.request":
            return replace(decision, allowed=not self.release_denied, approval_required=True)
        return decision

    def verify(self, context, reference, subject_digest, now):
        self.proofs.append((context, reference, subject_digest, now))
        proof = super().verify(context, reference, subject_digest, now)
        return replace(proof, **self.bad_proof) if self.bad_proof else proof


class Manifests:
    def __init__(self, host):
        self.host = host
        self.calls = []
        self.change = {}
        self.expire = False

    def verify(self, request, snapshot, context):
        assert self.host.fences > 0 and context == self.host.context
        self.calls.append((request, snapshot, context))
        if self.expire:
            self.host.now = LATER
        return replace(VerifiedReleaseManifest(snapshot.definition.graph_id, snapshot.revision,
            snapshot.definition_digest, MANIFEST), **self.change)


@pytest.fixture
def system():
    with tempfile.TemporaryDirectory(prefix="contexture-release-owner-") as temp:
        path = Path(temp) / "store"
        with FileJournal.open(path, schema=SCHEMA) as journal:
            host = ReleaseHost()
            manager = owner(journal, host, projections={PROJECTION_NAME: release_projection()})
            boot(manager)
            manifests = Manifests(host)
            coordinator = ReleaseIntentCoordinator(manager, manifests=manifests)
            yield path, journal, host, manager, manifests, coordinator
            manager.close()


def release(manager, *, key="release", epoch="0", graph_id="g", revision=None,
            policy="exact", target="target"):
    snapshot = manager.read(graph_id, revision)
    return {"schema": "contexture.release-request/v1", "graph_id": graph_id,
        "target_id": target, "revision": snapshot.revision,
        "definition_digest": snapshot.definition_digest, "manifest_digest": MANIFEST.manifest_digest,
        "expected_desired_epoch": epoch, "idempotency_key": key, "policy": policy,
        "reason": "publish pinned snapshot", "approval_ref": "approved"}


def test_atomic_receipt_manifest_proof_and_target_scoped_status(system):
    _, journal, host, manager, manifests, coordinator = system
    value = release(manager)
    result = coordinator.request(canonical_json(value)).as_json()
    record = journal.records()[-1]
    assert result["journal_seq"] == record["journal_seq"] == "2"
    assert result["status"] == "requested"
    assert record["command_receipt"]["operation_result"] == {"inline": result}
    assert record["command_receipt"]["request_digest"] == ReleaseRequest.from_json(value).digest()
    assert manager.receipt("g", "release") == record["command_receipt"]
    assert len(manifests.calls) == 1
    assert host.proofs[-1][2] == result["request_digest"]
    boot(manager, "secret")
    host.read_denied.add("secret")
    status = coordinator.status("g", "target")
    assert len(status["desired_releases"]) == len(status["release_operations"]) == 1
    assert not status["observations_history"]
    assert coordinator.status("g", "absent")["desired_releases"] == []


@pytest.mark.parametrize("policy", ["exact", "latest"])
def test_explicit_supersession_preserves_original_receipt(system, policy):
    _, journal, _, manager, _, coordinator = system
    value = release(manager, policy=policy)
    original = coordinator.request(value)
    successor = coordinator.request(release(manager, key="next", epoch="1", policy="latest"))
    assert successor.as_json()["release"]["release_epoch"] == "2"
    assert [e["type"] for e in journal.records()[-1]["events"]] == ["ReleaseSuperseded", "ReleaseRequested"]
    assert coordinator.request(value) == original
    operations = coordinator.status("g", "target")["release_operations"]
    assert [op["status"] for op in operations] == ["requested", "superseded", "requested"]
    assert operations[1]["superseded_by"] == "2"
    assert journal.watermark == "3"


def test_retry_before_cas_history_and_authority_rechecks_only_current_read(system):
    _, journal, host, manager, manifests, coordinator = system
    value = release(manager)
    accepted = coordinator.request(value)
    manager.commit(manager.preview(graph_request(manager), expires_at=LATER))
    host.context = replace(host.context, authority_revision="2")
    host.now = LATER
    host.release_denied = host.proof_denied = True
    host.calls.clear()
    assert coordinator.request({**value, "approval_ref": "renewed"}) == accepted
    assert [r.operation for r in host.calls] == ["graph.read"]
    assert len(manifests.calls) == 1 and journal.watermark == "3"
    host.read_denied.add("g")
    with pytest.raises(GraphError, match="Current admission"):
        coordinator.request(value)


def test_pinned_history_not_head_and_wrong_history_digest_refused(system):
    _, journal, _, manager, manifests, coordinator = system
    value = release(manager)
    manager.commit(manager.preview(graph_request(manager), expires_at=LATER))
    accepted = coordinator.request(value)
    assert manifests.calls[-1][1].revision == "1"
    assert manager.read("g").revision == "2"
    assert accepted.as_json()["release"]["release_epoch"] == "1"
    bad = {**value, "idempotency_key": "bad", "expected_desired_epoch": "1",
           "definition_digest": manager.read("g").definition_digest}
    with pytest.raises(GraphError) as error:
        coordinator.request(bad)
    assert error.value.code == "contract_mismatch" and journal.watermark == "3"


def test_threaded_same_base_has_one_winner(system):
    _, journal, _, manager, _, coordinator = system
    values = [release(manager, key=f"release-{i}") for i in range(8)]
    def run(value):
        try:
            return coordinator.request(value).as_json()["status"]
        except GraphError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, values))
    assert results.count("requested") == 1 and results.count("revision_conflict") == 7
    assert journal.watermark == "2"


@pytest.mark.parametrize("bad", ["denied", "approval", "revoked", "subject", "actor", "tenant",
    "authority", "expiry", "provider_expiry", "manifest", "binding_graph", "binding_revision",
    "binding_digest", "missing_history", "cas", "credentials"])
def test_refusals_do_not_append(system, bad):
    _, journal, host, manager, manifests, coordinator = system
    value = release(manager)
    if bad == "denied": host.release_denied = True
    elif bad == "approval": value.pop("approval_ref")
    elif bad == "revoked": host.proof_denied = True
    elif bad == "subject": host.bad_proof = {"subject_digest": "sha256:" + "0" * 64}
    elif bad == "actor": host.bad_proof = {"actor_id": "other"}
    elif bad == "tenant": host.bad_proof = {"tenant_id": "other"}
    elif bad == "authority": host.bad_proof = {"authority_revision": "2"}
    elif bad == "expiry": host.bad_proof = {"expires_at": NOW}
    elif bad == "provider_expiry": manifests.expire = True
    elif bad == "manifest": value["manifest_digest"] = "sha256:" + "0" * 64
    elif bad == "binding_graph": manifests.change = {"graph_id": "other"}
    elif bad == "binding_revision": manifests.change = {"revision": "2"}
    elif bad == "binding_digest": manifests.change = {"definition_digest": "sha256:" + "0" * 64}
    elif bad == "missing_history": value["revision"] = "99"
    elif bad == "cas": value["expected_desired_epoch"] = "1"
    elif bad == "credentials": value["actor_ref"] = "forged"
    with pytest.raises(GraphError):
        coordinator.request(value)
    assert journal.watermark == "1" and not manager.writes_stopped


def test_release_requires_no_blanket_read_but_retry_and_status_do(system):
    _, journal, host, manager, _, coordinator = system
    value = release(manager)
    boot(manager, "secret")
    host.read_denied.update({"g", "secret"})
    host.calls.clear()
    coordinator.request(value)
    assert all(call.operation == "release.request" for call in host.calls)
    for action in [lambda: coordinator.request(value), lambda: coordinator.status("g", "target")]:
        with pytest.raises(GraphError): action()
    assert journal.watermark == "3"


def test_conflict_digest_and_common_graph_command_namespace(system):
    _, journal, _, manager, _, coordinator = system
    value = release(manager)
    coordinator.request(value)
    with pytest.raises(GraphError) as error:
        coordinator.request({**value, "reason": "changed"})
    assert error.value.code == "idempotency_conflict" and journal.watermark == "2"
    manager.commit(manager.preview(graph_request(manager, key="command"), expires_at=LATER))
    with pytest.raises(GraphError) as error:
        coordinator.request(release(manager, key="command", epoch="1"))
    assert error.value.code == "idempotency_conflict" and journal.watermark == "3"


def test_cross_graph_key_conflict_never_reads_or_leaks_original_graph(system):
    _, journal, host, manager, _, coordinator = system
    coordinator.request(release(manager))
    boot(manager, "second")
    other = release(manager, graph_id="second")
    host.read_denied.add("g")
    host.calls.clear()
    with pytest.raises(GraphError) as error:
        coordinator.request(other)
    assert error.value.code == "idempotency_conflict"
    assert not host.calls and "g" not in str(error.value)
    assert journal.watermark == "3"


def test_different_actor_gets_own_receipt_namespace(system):
    _, journal, host, manager, _, coordinator = system
    value = release(manager)
    first = coordinator.request(value)
    host.context = replace(host.context, actor_id="other")
    second = coordinator.request({**value, "expected_desired_epoch": "1"})
    assert first != second and journal.watermark == "3"
    assert journal.records()[-1]["command_receipt"]["actor_ref"] == "tenant:other"


def test_recovery_rebuilds_exact_receipts_without_provider_or_policy(system):
    path, journal, host, manager, _, coordinator = system
    value = release(manager)
    first = coordinator.request(value)
    coordinator.request(release(manager, key="next", epoch="1"))
    expected = coordinator.status("g", "target")
    manager.close()
    journal.close()
    host.denied = host.proof_denied = True
    host.calls.clear()
    class NoManifest:
        def verify(self, request, snapshot, context):
            pytest.fail("Replay or retry called compile provider")
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host, projections={PROJECTION_NAME: release_projection()})
        assert not host.calls
        release_owner = ReleaseIntentCoordinator(recovered, manifests=NoManifest())
        host.denied = False
        assert release_owner.request(value) == first
        assert release_owner.status("g", "target") == expected
        recovered.close()


@pytest.mark.parametrize("stage", ["record.before_rename", "record.renamed", "record.directory_synced"])
def test_unknown_stops_releases_and_recovers_receipt(system, stage):
    path, journal, host, manager, _, coordinator = system
    value = release(manager)
    def fault(step):
        if step == stage:
            raise OSError("sync failure")
    journal._fault = fault
    with pytest.raises((OSError, CommitUnknown)):
        coordinator.request(value)
    assert manager.writes_stopped
    assert coordinator.status("g", "target")["desired_releases"] == []
    with pytest.raises(GraphError) as error:
        coordinator.request(value)
    assert error.value.code == "commit_unknown"
    manager.close()
    journal.close()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host, projections={PROJECTION_NAME: release_projection()})
        retry = ReleaseIntentCoordinator(recovered, manifests=Manifests(host))
        result = retry.request(value)
        assert result.as_json()["journal_seq"] == "2" and reopened.watermark == "2"
        receipt = recovered.receipt("g", "release")
        assert receipt is not None
        assert receipt["operation_result"]["inline"] == result.as_json()
        recovered.close()


def test_missing_projection_refuses_before_write(system):
    _, journal, host, manager, _, _ = system
    manager.close()
    without_projection = owner(journal, host)
    coordinator = ReleaseIntentCoordinator(without_projection, manifests=Manifests(host))
    with pytest.raises(GraphError) as error:
        coordinator.request(release(without_projection))
    assert error.value.code == "unsupported_contract" and journal.watermark == "1"
    without_projection.close()


@pytest.mark.parametrize("mode", ["raise", "malformed_result"])
def test_malformed_projection_candidate_has_no_durable_partial(system, mode):
    _, journal, host, manager, _, _ = system
    manager.close()
    projection = release_projection()
    def malformed(state, event, record):
        reduce_release_event(state, event, record)
        if mode == "malformed_result":
            return {}
        raise GraphError("schema_invalid", "Malformed registered projection")
    replacement = owner(journal, host, projections={PROJECTION_NAME:
        EventProjection(projection.event_types, projection.initial, malformed)})
    coordinator = ReleaseIntentCoordinator(replacement, manifests=Manifests(host))
    with pytest.raises(GraphError):
        coordinator.request(release(replacement))
    assert journal.watermark == "1" and not replacement.writes_stopped
    assert replacement.receipt("g", "release") is None
    replacement.close()


def test_projection_and_original_receipt_are_detached(system):
    _, _, _, manager, _, coordinator = system
    value = release(manager)
    original = coordinator.request(value)
    status = coordinator.status("g", "target")
    status["desired_releases"].clear()
    result = original.as_json()
    result["status"] = "forged"
    assert coordinator.request(value).as_json()["status"] == "requested"
    assert len(coordinator.status("g", "target")["desired_releases"]) == 1


def test_old_boot_observation_replays_as_history_only(system):
    path, journal, host, manager, _, coordinator = system
    value = release(manager)
    result = coordinator.request(value).as_json()
    old_boot = "12345678-1234-4234-8234-123456789abc"
    observation = {"release": result["release"], "instance_id": "instance", "boot_id": old_boot,
        "generation_id": old_boot, "revision": value["revision"],
        "definition_digest": value["definition_digest"], "manifest_digest": value["manifest_digest"],
        "observed_at": NOW}
    with manager.release_boundary() as boundary:
        snapshot = boundary.snapshot("g")
        admission = AdmissionRequest(boundary.context, "g", "release.observe", (), snapshot.revision,
            snapshot.definition_digest, snapshot.definition_digest, (), (), None, None)
        boundary.append([{"type": "ReplicaObserved", "observation": observation}],
                        request=admission, correlation_id="trace")
    manager.close()
    journal.close()
    host.denied = host.proof_denied = True
    host.calls.clear()
    with FileJournal.open(path, schema=SCHEMA) as reopened:
        recovered = owner(reopened, host, projections={PROJECTION_NAME: release_projection()})
        assert not host.calls
        host.denied = False
        status = ReleaseIntentCoordinator(recovered, manifests=Manifests(host)).status("g", "target")
        assert status["observations_history"] == [observation]
        assert set(status) == {"desired_releases", "release_operations",
                               "prepare_attempt_history", "observations_history"}
        recovered.close()


def test_target_epoch_is_independent_and_read_boundary_cannot_append(system):
    _, journal, _, manager, _, coordinator = system
    coordinator.request(release(manager))
    second = coordinator.request(release(manager, key="other-target", target="second"))
    assert second.as_json()["release"]["release_epoch"] == "1"
    assert len(coordinator.status("g", "target")["release_operations"]) == 1
    with manager.release_boundary(write=False) as boundary:
        snapshot = boundary.snapshot("g")
        admission = AdmissionRequest(boundary.context, "g", "release.observe", (), snapshot.revision,
            snapshot.definition_digest, snapshot.definition_digest, (), (), None, None)
        with pytest.raises(GraphError) as error:
            boundary.append([], request=admission, correlation_id="trace")
        assert error.value.code == "permission_denied"
    assert journal.watermark == "3"