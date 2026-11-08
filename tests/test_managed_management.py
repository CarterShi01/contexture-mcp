"""Native management with a real owner, strict FileJournal and Host authority."""

from dataclasses import replace
from pathlib import Path
import tempfile

import pytest

from contexture.application.managed_management import ManagedManagementService, decode_request
from contexture.application.managed_releases import (
    PROJECTION_NAME, ReleaseIntentCoordinator, release_projection,
)
from contexture.core.graph.errors import GraphError
from contexture.server.managed_journal import FileJournal
from tests.test_managed_owner import Host, LATER, NOW, SCHEMA, boot, owner, request
from tests.test_managed_release_owner import Manifests, release


@pytest.fixture
def management():
    with tempfile.TemporaryDirectory(prefix="management-") as temp:
        with FileJournal.open(Path(temp) / "store", schema=SCHEMA) as journal:
            host = Host()
            manager = owner(journal, host, projections={PROJECTION_NAME: release_projection()})
            boot(manager)
            service = ManagedManagementService(manager, mode="write",
                releases=ReleaseIntentCoordinator(manager, manifests=Manifests(host)))
            try:
                yield service, manager, host, journal
            finally:
                service.close()
                manager.close()


def preview(service, manager, **kwargs):
    return service.execute("preview", {"transaction": request(manager, **kwargs),
                                      "expires_at": LATER})["result"]


def test_native_preview_commit_history_diff_receipt_revert(management):
    service, manager, _, journal = management
    proposal = preview(service, manager)
    assert journal.watermark == "1"
    assert proposal["review"]["base_revision"] == "1"
    result = service.execute("commit", {"preview_id": proposal["preview_id"]})
    assert result["stage"] == "committed" and result["result"]["revision"] == "2"
    assert service.execute("receipt", {"graph_id": "g", "idempotency_key": "key"})["result"] == result["result"]
    page = service.execute("history", {"graph_id": "g", "limit": 1})["result"]
    assert not page["complete"] and page["next_after"] == "1"
    assert service.execute("history", {"graph_id": "g", "after": "1"})["result"]["complete"]
    delta = service.execute("diff", {"graph_id": "g", "before": "1", "after": "2"})["result"]
    assert delta["nodes"][0]["field"] == "/name"
    revert = service.execute("revert", {"graph_id": "g", "target_revision": "1",
        "idempotency_key": "revert", "reason": "restore", "correlation_id": "trace",
        "expires_at": LATER})
    assert revert["stage"] == "preview" and journal.watermark == "2"
    service.execute("commit", {"preview_id": revert["result"]["preview_id"]})
    assert manager.read("g").revision == "3"
    assert manager.read("g").definition.nodes["r"].name == "root"
    assert service.execute("status", {"graph_id": "g"})["result"]["management_mode"] == "write"
    assert service.execute("head", {"graph_id": "g"})["result"]["revision"] == "3"


@pytest.mark.parametrize("change", ["authority", "actor", "tenant", "denied", "expiry", "cas"])
def test_retained_preview_current_authority_negative(management, change):
    service, manager, host, journal = management
    proposal = preview(service, manager)
    if change == "authority": host.context = replace(host.context, authority_revision="2")
    elif change == "actor": host.context = replace(host.context, actor_id="other")
    elif change == "tenant": host.context = replace(host.context, tenant_id="other")
    elif change == "denied": host.denied = True
    elif change == "expiry": host.now = LATER
    else: manager.commit(manager.preview(request(manager, key="concurrent"), expires_at=LATER))
    before = journal.watermark
    with pytest.raises(GraphError):
        service.execute("commit", {"preview_id": proposal["preview_id"]})
    assert journal.watermark == before


def test_exact_retry_after_expiry_cas_and_authority_change_needs_current_read(management):
    service, manager, host, journal = management
    proposal = preview(service, manager)
    args = {"preview_id": proposal["preview_id"]}
    accepted = service.execute("commit", args)
    manager.commit(manager.preview(request(manager, key="later", name="later"), expires_at=LATER))
    host.now = LATER
    host.context = replace(host.context, authority_revision="2")
    assert service.execute("commit", args) == accepted
    assert journal.watermark == "3"
    host.denied = True
    with pytest.raises(GraphError): service.execute("commit", args)


def test_readonly_door_payload_identity_and_lifecycle(management):
    service, manager, host, journal = management
    debug = ManagedManagementService(manager, mode="read_only")
    for command in ("preview", "commit", "revert", "release"):
        with pytest.raises(GraphError) as error: debug.execute(command, {})
        assert error.value.code == "permission_denied"
    for key in ("actor_ref", "identity", "authorities", "factory", "store"):
        with pytest.raises(GraphError): service.execute("head", {"graph_id": "g", key: "secret"})
    transaction = request(manager)
    transaction["actor_ref"] = "secret"
    with pytest.raises(GraphError):
        service.execute("preview", {"transaction": transaction, "expires_at": LATER})
    assert journal.watermark == "1"
    debug.close()
    assert manager.read("g").revision == "1"
    with pytest.raises(GraphError): debug.execute("head", {"graph_id": "g"})
    host.denied = True
    for command in ("head", "status", "history", "receipt", "diff"):
        args = {"graph_id": "g"}
        if command == "receipt": args["idempotency_key"] = "key"
        if command == "diff": args.update(before="1", after="1")
        with pytest.raises(GraphError): service.execute(command, args)


def test_preview_budget_unknown_handle_and_strict_raw_json(management):
    service, manager, _, journal = management
    bounded = ManagedManagementService(manager, mode="write", max_previews=1)
    preview(bounded, manager)
    with pytest.raises(GraphError) as error: preview(bounded, manager, key="another")
    assert error.value.code == "resource_busy" and journal.watermark == "1"
    with pytest.raises(GraphError): service.execute("commit", {"preview_id": "forged"})
    for raw in (b'{"graph_id":"g","graph_id":"g"}', b'{"n":9007199254740993}', b'{"n":NaN}'):
        with pytest.raises(GraphError): decode_request(raw)
    bounded.close()


def test_release_is_durable_requested_not_observed(management):
    service, manager, host, journal = management
    args = {"request": release(manager)}
    accepted = service.execute("release", args)
    assert accepted["stage"] == accepted["result"]["status"] == "requested"
    assert manager.read("g").revision == "1" and journal.watermark == "2"
    assert service.execute("release", args) == accepted
    status = service.execute("release-status", {"graph_id": "g", "target_id": "target"})
    assert not status["result"]["observations_history"]
    assert "approval_ref" not in status["result"]["desired_releases"][0]["request"]
    assert "approval_ref" in service.releases.status("g", "target")["desired_releases"][0]["request"]
    host.denied = True
    with pytest.raises(GraphError): service.execute("release", args)
    absent = ManagedManagementService(manager, mode="write")
    with pytest.raises(GraphError) as error: absent.execute("release", args)
    assert error.value.code == "unsupported_contract"