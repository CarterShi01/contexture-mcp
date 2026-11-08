"""Executable ordinary mutations, exact facts and proposal-only admission."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from typing import Any

import pytest

from contexture.core.graph.canonical import MAX_COUNTER, canonical_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import RevisionState, apply_facts, change_event
from contexture.core.graph.history import History
from contexture.core.graph.model import BindingDescriptor, GraphDefinition, PublicationContract
from contexture.core.graph.policy import (
    AdmissionDecision, AdmissionRequest, VerifiedApproval, VerifiedContext,
)
from contexture.core.graph.transactions import (
    Limits, TransactionEnvelope, admit_transition, prepare, prepare_revert, preview,
)

NOW = "2026-11-05T00:00:00Z"
LATER = "2026-11-05T01:00:00Z"
CTX = VerifiedContext("actor", "tenant", "1", frozenset({"graph.roots"}))


def node(identity: str, **fields: Any) -> dict[str, Any]:
    return {"node_id": identity, "kind": "role", "name": identity,
            "description": "description", "instructions": "program", **fields}


def state(*nodes: dict[str, Any], revision: str = "1", roots: Any = None,
          publications: Any = None, presentations: Any = None) -> RevisionState:
    return RevisionState(GraphDefinition.from_json({
        "schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": "g",
        "application_name": "app", "mode": "disclosure_only",
        "nodes": list(nodes) or [node("r", mutable=True)],
        "roots": roots if roots is not None else [{"node_id": "r", "plane": "model"}],
        "publications": publications or [],
        "presentations": presentations or {"default_locale": "en", "catalogs": [
            {"locale": "en", "entries": []}]},
    }), revision)


class Allow:
    def __init__(self) -> None:
        self.requests: list[AdmissionRequest] = []
        self.revision = "1"
        self.required = False
        self.deny = False

    def decide(self, request: AdmissionRequest) -> AdmissionDecision:
        self.requests.append(request)
        return AdmissionDecision(not self.deny, self.revision,
            frozenset((*request.impact, *request.reference_targets)),
            self.required and request.operation != "graph.read")


def envelope(base: RevisionState, *commands: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"schema": "contexture.transaction/v1", "graph_id": base.definition.graph_id,
            "base_revision": base.revision, "idempotency_key": "key", "commands": list(commands),
            "reason": "test", "correlation_id": "trace", **fields}


def build(base: RevisionState, *commands: dict[str, Any], **kwargs: Any) -> Any:
    return prepare(base, envelope(base, *commands), context=CTX, admission=Allow(), **kwargs)


def roundtrip(prepared: Any) -> RevisionState:
    policy = Allow()
    receipt = preview(prepared, context=CTX, admission=policy, now=NOW, expires_at=LATER)
    transition = admit_transition(receipt, prepared.base, context=CTX, admission=policy, now=NOW)
    assert transition.status == "admitted"
    assert not hasattr(transition, "commit") and not hasattr(transition, "journal_seq")
    if prepared.change.noop:
        assert transition.result_revision == prepared.base.revision
        return prepared.base
    result = apply_facts(prepared.base, transition.change.facts,
        tombstones=transition.change.tombstones, revision=transition.result_revision,
        before_digest=prepared.base.definition_digest,
        after_digest=transition.change.candidate.digest(),
        resolved_fact_digest=transition.change.facts_digest)
    assert result.definition == prepared.change.candidate
    return result


def test_real_create_attach_update_rename_and_template_switch() -> None:
    base = state()
    request = envelope(base,
        {"op": "create_node", "node": node("a")},
        {"op": "attach_node", "node_id": "a", "target": {"parent_id": "r", "slot": "children", "position": 0}},
        {"op": "update_node", "node_id": "a", "set_fields": {"description": "new",
            "instruction_template": {"parts": [{"kind": "NodeRef", "target_id": "r"}]}},
            "unset_fields": ["instructions"]},
        {"op": "rename_node", "node_id": "a", "new_name": "renamed"})
    prepared = prepare(base, request, context=CTX, admission=Allow())
    request["commands"][0]["node"]["description"] = "caller mutation"
    result = roundtrip(prepared)
    assert result.definition.nodes["a"].name == "renamed"
    assert result.definition.nodes["a"].description == "new"
    assert result.definition.nodes["a"].instructions is None
    assert base.definition.nodes.keys() == {"r"} and base.revision == "1"
    with pytest.raises(TypeError):
        value: Any = prepared.envelope.value
        value["reason"] = "tamper"
    with pytest.raises(TypeError):
        subject: Any = prepared.approval_subject
        subject["base_revision"] = "8"


def test_all_relation_mutations_move_after_removal_and_roots() -> None:
    base = state(node("r", mutable=True, members={"children": ["a", "b", "c"]}),
                 node("a"), node("b"), node("c"))
    p = build(base,
        {"op": "move_node", "node_id": "a", "target": {"parent_id": "r", "slot": "children", "position": 2}},
        {"op": "reorder_members", "parent_id": "r", "slot": "children", "node_ids": ["c", "b", "a"]},
        {"op": "detach_node", "node_id": "b"},
        {"op": "add_root", "node_id": "b", "plane": "prompt", "position": 1},
        {"op": "remove_root", "node_id": "b"},
        {"op": "attach_node", "node_id": "b", "target": {"parent_id": "r", "slot": "children", "position": 0}},
        {"op": "add_use", "source_id": "a", "target_id": "b"},
        {"op": "add_use", "source_id": "b", "target_id": "a", "position": 0},
        {"op": "remove_use", "source_id": "a", "target_id": "b"})
    result = roundtrip(p)
    members = result.definition.nodes["r"].members
    assert members is not None and members.children == ("b", "c", "a")
    assert result.definition.nodes["b"].uses == ("a",)
    moved = build(base, {"op": "move_node", "node_id": "a", "target": {
        "parent_id": "r", "slot": "children", "position": 2}})
    moved_members = roundtrip(moved).definition.nodes["r"].members
    assert moved_members is not None and moved_members.children == ("b", "c", "a")


def test_new_typed_slots_and_singleton_occupancy() -> None:
    base = state()
    commands: list[dict[str, Any]] = []
    for identity, slot, kind in [("pre", "pre_process", "role"), ("post", "post_process", "role"),
                                 ("s", "skills", "skill"), ("t", "tools", "tool")]:
        n = node(identity, kind=kind)
        if kind == "role":
            n["role_variant"] = slot
        if kind == "tool":
            del n["instructions"]
        commands.extend([{"op": "create_node", "node": n}, {"op": "attach_node", "node_id": identity,
            "target": {"parent_id": "r", "slot": slot, "position": 0}}])
    result = roundtrip(build(base, *commands))
    members = result.definition.nodes["r"].members
    assert members is not None
    assert [n for _, _, n in members.placements()] == ["pre", "post", "s", "t"]
    with pytest.raises(GraphError, match="Singleton"):
        build(result, {"op": "create_node", "node": node("p2", role_variant="pre_process")},
            {"op": "attach_node", "node_id": "p2", "target": {
                "parent_id": "r", "slot": "pre_process", "position": 0}})


def test_publication_and_presentation_mutations() -> None:
    base = state()
    publication = {"kind": "prompt", "publication_id": "p", "name": "entry", "description": "entry",
                   "opens_id": "r", "model_may_open": True}
    result = roundtrip(build(base, {"op": "put_publication", "publication": publication},
        {"op": "put_presentation", "locale": "EN", "presentation": {"node_id": "r", "title": "Title"}}))
    assert result.definition.publications[0].publication_id == "p"
    assert result.definition.presentations.catalogs[0].entries[0].title == "Title"
    restored = roundtrip(build(result, {"op": "remove_publication", "publication_id": "p"},
        {"op": "remove_presentation", "locale": "en", "node_id": "r"}))
    assert restored.definition.digest() == base.definition.digest()


def test_delete_exact_cascade_and_history_restoration() -> None:
    initial = state(node("r", mutable=True, members={"children": ["a", "x"]}),
        node("a", members={"children": ["b"]}), node("b"), node("x", uses=["b"]))
    command = {"op": "delete_nodes", "targets": ["a"], "deleted_ids": ["a", "b"],
        "policy": "explicit_cascade", "disconnections": [
            {"source_id": "r", "target_id": "a", "relation": "containment"},
            {"source_id": "x", "target_id": "b", "relation": "uses"}]}
    p = build(initial, command)
    deleted = roundtrip(p)
    assert set(deleted.definition.nodes) == {"r", "x"}
    assert sorted(str(t["node_id"]) for t in deleted.tombstones) == ["a", "b"]
    history = History.seed(initial, journal_seq="1").append(change_event(initial, p.change,
        command_digest=p.envelope.command_digest, reason="test"), journal_seq="2", tombstones=p.change.tombstones)
    restored = build(deleted, {"op": "restore_nodes", "revision": "1", "node_ids": ["a", "b"]},
        {"op": "attach_node", "node_id": "a", "target": {"parent_id": "r", "slot": "children", "position": 0}},
        history=history)
    assert [f["type"] for f in restored.change.facts][:2] == ["NodeRestored", "NodeRestored"]
    assert restored.change.tombstones == deleted.tombstones
    # Commit admission must re-read the same authorized retained history.
    receipt = preview(restored, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
    transition = admit_transition(receipt, deleted, context=CTX, admission=Allow(), now=NOW, history=history)
    assert transition.result_revision == "3"
    with pytest.raises(GraphError):
        build(deleted, {"op": "create_node", "node": node("a")})
    with pytest.raises(GraphError):
        build(initial, {**command, "disconnections": []})
    with pytest.raises(GraphError):
        build(initial, {**command, "deleted_ids": ["a"]})
    with pytest.raises(GraphError):
        build(initial, {**command, "policy": "reject_references"})


@pytest.mark.parametrize("op", ["replace_subtree", "import", "reconcile"])
def test_real_exact_mapping_whole_graph_payload(op: str) -> None:
    base = state()
    target = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))
    command = {"op": op, "id_mapping": {"retained_ids": ["r"], "new_ids": ["a"], "deleted_ids": []},
               "payload": {"inline": target.definition.as_json()}}
    command["anchor_id" if op == "replace_subtree" else "scope_ids"] = "r" if op == "replace_subtree" else ["r"]
    assert roundtrip(build(base, command)).definition == target.definition
    with pytest.raises(GraphError, match="partition"):
        build(base, {**command, "id_mapping": {"retained_ids": [], "new_ids": ["a"], "deleted_ids": []}})


def test_staged_payload_exact_bytes_and_no_provider_refusal() -> None:
    base = state()
    target = state(node("r", mutable=True, description="staged"))
    data = canonical_json(target.definition.as_json())
    command = {"op": "import", "scope_ids": ["r"],
        "id_mapping": {"retained_ids": ["r"], "new_ids": [], "deleted_ids": []},
        "payload": {"blob": {"digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": str(len(data)), "media_type": "application/json"}}}

    class Payload:
        def read(self, reference: Any) -> bytes:
            return data

    prepared = build(base, command, payloads=Payload())
    assert prepared.change.candidate == target.definition
    with pytest.raises(GraphError, match="provider"):
        build(base, command)


def test_preview_recheck_authority_actor_cas_expiry_and_no_fake_store() -> None:
    base, policy = state(), Allow()
    p = build(base, {"op": "rename_node", "node_id": "r", "new_name": "new"})
    proof = VerifiedApproval(CTX.actor_id, CTX.tenant_id, "1", p.approval_subject_digest, LATER)
    policy.required = True
    receipt = preview(p, context=CTX, admission=policy, now=NOW, expires_at=LATER, approval=proof)
    assert admit_transition(receipt, base, context=CTX, admission=policy, now=NOW, approval=proof).result_revision == "2"
    assert base.revision == "1" and base.definition.nodes["r"].name == "r"
    for ctx in (replace(CTX, actor_id="other"), replace(CTX, authority_revision="2"),
                replace(CTX, tenant_id="other")):
        with pytest.raises(GraphError):
            admit_transition(receipt, base, context=ctx, admission=policy, now=NOW, approval=proof)
    with pytest.raises(GraphError) as error:
        admit_transition(receipt, replace(base, revision="2"), context=CTX, admission=policy, now=NOW, approval=proof)
    assert error.value.code == "revision_conflict"
    with pytest.raises(GraphError):
        admit_transition(receipt, base, context=CTX, admission=policy, now=LATER, approval=proof)
    with pytest.raises(GraphError):
        admit_transition(receipt, base, context=CTX, admission=policy, now=NOW,
                         approval=replace(proof, actor_id="other"))
    policy.revision = "2"
    with pytest.raises(GraphError) as error:
        admit_transition(receipt, base, context=CTX, admission=policy, now=NOW, approval=proof)
    assert error.value.code == "approval_stale"


def test_noop_atomic_failure_and_identity_recreation() -> None:
    base = state()
    noop = build(base, {"op": "create_node", "node": node("tmp")},
        {"op": "delete_nodes", "targets": ["tmp"], "deleted_ids": ["tmp"],
         "policy": "reject_references", "disconnections": []})
    assert noop.change.noop and noop.change.tombstones == ()
    roundtrip(noop)
    assert build(base, {"op": "rename_node", "node_id": "r", "new_name": "r"}).change.noop
    with pytest.raises(GraphError):
        build(base, {"op": "create_node", "node": node("tmp")},
            {"op": "delete_nodes", "targets": ["tmp"], "deleted_ids": ["tmp"],
             "policy": "reject_references", "disconnections": []},
            {"op": "create_node", "node": node("tmp")})
    before = canonical_json(base.definition.as_json())
    with pytest.raises(GraphError):
        build(base, {"op": "rename_node", "node_id": "r", "new_name": "changed"},
            {"op": "create_node", "node": node("unowned")})
    assert canonical_json(base.definition.as_json()) == before


@pytest.mark.parametrize("command", [
    {"op": "update_node", "node_id": "r", "set_fields": {"mutable": True}, "unset_fields": []},
    {"op": "update_node", "node_id": "r", "set_fields": {"members": {}}, "unset_fields": []},
    {"op": "rename_node", "node_id": "r", "new_name": "new", "actor": "admin"},
    {"op": "attach_node", "node_id": "r", "target": {"plane": "model", "position": True}},
    {"op": "create_node", "node": node("x", uses=["r"])},
    {"op": "create_node", "node": node("x", members={"children": ["r"]})},
    {"op": "add_use", "source_id": "r", "target_id": "missing"},
    {"op": "remove_use", "source_id": "r", "target_id": "r"},
    {"op": "reorder_members", "parent_id": "r", "slot": "children", "node_ids": ["r"]},
    {"op": "remove_publication", "publication_id": "missing"},
    {"op": "remove_presentation", "locale": "en", "node_id": "r"},
    {"op": "restore_nodes", "revision": "01", "node_ids": ["r"]},
    {"op": "eval", "code": "never"},
])
def test_strict_shapes_and_invalid_operations_fail_closed(command: Any) -> None:
    with pytest.raises(GraphError):
        build(state(), command)


def test_digest_scope_raw_decode_limits_and_counter_overflow() -> None:
    base = state()
    command = {"op": "rename_node", "node_id": "r", "new_name": "new"}
    a = TransactionEnvelope.from_json(envelope(base, command))
    b = TransactionEnvelope.from_json(envelope(base, command, idempotency_key="different",
                                               correlation_id="different", approval_ref="renewed"))
    assert a.command_digest == b.command_digest
    with pytest.raises(GraphError):
        TransactionEnvelope.from_json(b'{"schema":1,"schema":2}')
    with pytest.raises(GraphError):
        build(base, command, limits=Limits(max_transaction_bytes=10))
    with pytest.raises(GraphError):
        build(base, command, limits=Limits(max_resolved_bytes=1))
    with pytest.raises(GraphError):
        p = build(replace(base, revision=str(MAX_COUNTER)), command)
        receipt = preview(p, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
        admit_transition(receipt, p.base, context=CTX, admission=Allow(), now=NOW)


def test_catalog_creation_default_locale_and_complete_replay() -> None:
    base = state()
    translated = roundtrip(build(base, {"op": "put_presentation", "locale": "fr",
        "presentation": {"node_id": "r", "title": "fr"}}))
    assert [c.locale for c in translated.definition.presentations.catalogs] == ["en", "fr"]
    target = base.definition.as_json()
    target["presentations"] = {"default_locale": "fr", "catalogs": [{"locale": "en", "entries": []}]}
    prepared = build(base, {"op": "import", "scope_ids": ["r"],
        "id_mapping": {"retained_ids": ["r"], "new_ids": [], "deleted_ids": []},
        "payload": {"inline": target}})
    assert [f["type"] for f in prepared.change.facts] == ["PresentationsAssigned"]
    assert roundtrip(prepared).definition.presentations.default_locale == "fr"


def test_forward_template_reference_and_unowned_draft_final_validation() -> None:
    base = state()
    with pytest.raises(GraphError) as error:
        build(base, {"op": "update_node", "node_id": "r", "unset_fields": ["instructions"],
            "set_fields": {"instruction_template": {"parts": [{"kind": "NodeRef", "target_id": "later"}]}}},
            {"op": "create_node", "node": node("later")},
            {"op": "attach_node", "node_id": "later", "target": {
                "parent_id": "r", "slot": "children", "position": 0}})
    assert error.value.code == "reference_not_found"
    with pytest.raises(GraphError):
        build(base, {"op": "detach_node", "node_id": "r"})


def test_delete_requires_explicit_presentation_and_program_repair() -> None:
    base = state(
        node("r", mutable=True, members={"children": ["a"]}), node("a"),
        presentations={"default_locale": "en", "catalogs": [{"locale": "en", "entries": [
            {"node_id": "a", "title": "translated"}]}]})
    delete = {"op": "delete_nodes", "targets": ["a"], "deleted_ids": ["a"],
        "policy": "explicit_cascade", "disconnections": [{"source_id": "r", "target_id": "a", "relation": "containment"}]}
    with pytest.raises(GraphError, match="presentations"):
        build(base, delete)
    result = roundtrip(build(base, {"op": "remove_presentation", "locale": "en", "node_id": "a"}, delete))
    assert "a" not in result.definition.nodes
    x = node("x")
    del x["instructions"]
    x["instruction_template"] = {"parts": [{"kind": "NodeRef", "target_id": "a"}]}
    references = state(node("r", mutable=True, members={"children": ["a", "x"]}), node("a"), x)
    with pytest.raises(GraphError, match="NodeRef"):
        build(references, delete)
    result = roundtrip(build(references, {"op": "update_node", "node_id": "x",
        "set_fields": {"instructions": "repaired"}, "unset_fields": ["instruction_template"]}, delete))
    assert "a" not in result.definition.nodes


def test_historical_restore_requires_real_tombstones_and_current_policy() -> None:
    initial = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))
    history = History.seed(initial, journal_seq="1")
    with pytest.raises(GraphError, match="tombstone"):
        build(state(revision="2"), {"op": "restore_nodes", "revision": "1", "node_ids": ["a"]}, history=history)
    with pytest.raises(GraphError, match="provider"):
        build(state(revision="2"), {"op": "restore_nodes", "revision": "1", "node_ids": ["a"]})


def test_approval_and_preview_tampering_refuse() -> None:
    base, policy = state(), Allow()
    prepared = build(base, {"op": "rename_node", "node_id": "r", "new_name": "new"})
    policy.required = True
    with pytest.raises(GraphError) as error:
        preview(prepared, context=CTX, admission=policy, now=NOW, expires_at=LATER)
    assert error.value.code == "approval_stale"
    receipt = preview(prepared, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
    other = build(base, {"op": "rename_node", "node_id": "r", "new_name": "different"})
    forged = replace(receipt, prepared=replace(prepared, change=other.change))
    with pytest.raises(GraphError) as error:
        admit_transition(forged, base, context=CTX, admission=Allow(), now=NOW)
    assert error.value.code == "approval_stale"


def test_scoped_payload_noop_is_supported() -> None:
    base = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))
    command = {"op": "reconcile", "scope_ids": ["a"],
        "id_mapping": {"retained_ids": ["a"], "new_ids": [], "deleted_ids": []},
        "payload": {"inline": base.definition.as_json()}}
    assert build(base, command).change.noop
    command["op"] = "replace_subtree"
    del command["scope_ids"]
    command["anchor_id"] = "a"
    assert roundtrip(build(base, command)) == base


def test_transient_field_writes_validate_only_final_candidate() -> None:
    base = state()
    result = roundtrip(build(base,
        {"op": "update_node", "node_id": "r", "set_fields": {}, "unset_fields": ["instructions"]},
        {"op": "update_node", "node_id": "r", "set_fields": {"instructions": "final"}, "unset_fields": []}))
    assert result.definition.nodes["r"].instructions == "final"
    with pytest.raises(GraphError):
        build(base,
            {"op": "update_node", "node_id": "r", "set_fields": {"instructions": 123}, "unset_fields": []},
            {"op": "update_node", "node_id": "r", "set_fields": {"instructions": "final"}, "unset_fields": []})


def test_publication_reorder_complete_final_positions_replay() -> None:
    publications = [{"kind": "prompt", "publication_id": identity, "name": identity,
        "description": identity, "opens_id": "r", "model_may_open": True} for identity in ("p", "q")]
    base = state(publications=publications)
    target: Any = base.definition.as_json()
    target["publications"].reverse()
    prepared = build(base, {"op": "import", "scope_ids": ["r"],
        "id_mapping": {"retained_ids": ["r"], "new_ids": [], "deleted_ids": []},
        "payload": {"inline": target}})
    assert [p.publication_id for p in roundtrip(prepared).definition.publications] == ["q", "p"]


def test_payload_tampering_and_authority_revocation_with_same_version() -> None:
    base = state()
    data = canonical_json(base.definition.as_json())
    command = {"op": "import", "scope_ids": ["r"],
        "id_mapping": {"retained_ids": ["r"], "new_ids": [], "deleted_ids": []},
        "payload": {"blob": {"digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": str(len(data)), "media_type": "application/json"}}}

    class BadPayload:
        def read(self, reference: Any) -> bytes:
            return b"x" * len(data)

    with pytest.raises(GraphError, match="immutable reference"):
        build(base, command, payloads=BadPayload())
    prepared = build(base, {"op": "rename_node", "node_id": "r", "new_name": "new"})
    receipt = preview(prepared, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)

    class Revoked(Allow):
        def decide(self, request: AdmissionRequest) -> AdmissionDecision:
            if request.operation == "graph.transition":
                return AdmissionDecision(False, "1", frozenset())
            return super().decide(request)

    with pytest.raises(GraphError) as error:
        admit_transition(receipt, base, context=CTX, admission=Revoked(), now=NOW)
    assert error.value.code == "permission_denied"


def test_runtime_publications_require_exact_pinned_contracts() -> None:
    binding = {"kind": "registered", "binding_id": "read", "version": "1",
               "portability": {"kind": "portable", "contract_id": "read-contract"}}
    wire: Any = state().definition.as_json()
    wire["mode"] = "runtime"
    wire["nodes"].append({"node_id": "t", "kind": "tool", "name": "t", "description": "tool",
                          "binding": binding})
    wire["nodes"][0]["members"]["tools"] = ["t"]
    base = RevisionState(GraphDefinition.from_json(wire), "1")
    contracts = {"t": PublicationContract(BindingDescriptor.from_json(binding), "read_only", True)}
    commands = [{"op": "put_publication", "publication": {
        "kind": "resource", "publication_id": "resource", "name": "resource", "uri": "urn:test:t",
        "mime_type": "text/plain", "opens_id": "t"}}, {"op": "put_publication", "publication": {
        "kind": "route", "publication_id": "route", "method": "GET", "path": "/read", "opens_id": "t"}}]
    with pytest.raises(GraphError) as error:
        build(base, *commands)
    assert error.value.code == "binding_contract_required"
    prepared = build(base, *commands, publication_contracts=contracts)
    receipt = preview(prepared, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
    admitted = admit_transition(receipt, base, context=CTX, admission=Allow(), now=NOW,
                                publication_contracts=contracts)
    assert len(admitted.change.candidate.publications) == 2
    result = apply_facts(base, admitted.change.facts, tombstones=admitted.change.tombstones,
        revision="2", before_digest=base.definition_digest, after_digest=admitted.change.candidate.digest(),
        publication_contracts=contracts)
    assert result.definition == admitted.change.candidate
    # A GET publication cannot borrow a write binding's evidence.
    with pytest.raises(GraphError):
        build(base, *commands, publication_contracts={
            "t": PublicationContract(BindingDescriptor.from_json(binding), "write", False)})


def test_preparation_does_not_read_schema_files_or_execute_code(monkeypatch: Any) -> None:
    base = state()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Pure transaction attempted file/eval access")

    monkeypatch.setattr("builtins.open", forbidden)
    monkeypatch.setattr("builtins.eval", forbidden)
    p = build(base, {"op": "update_node", "node_id": "r", "set_fields": {
        "instructions": "__import__('os').system('never execute')"}, "unset_fields": []})
    assert p.change.candidate.nodes["r"].instructions == "__import__('os').system('never execute')"


def scoped_command(op: str, target: Any, *, seeds: Any = None,
                   retained: Any = None, new: Any = None, deleted: Any = None) -> Any:
    result = {"op": op, "payload": {"inline": target}, "id_mapping": {
        "retained_ids": ["a"] if retained is None else retained,
        "new_ids": [] if new is None else new, "deleted_ids": [] if deleted is None else deleted}}
    if op == "replace_subtree":
        result["anchor_id"] = "a" if seeds is None else seeds
    else:
        result["scope_ids"] = ["a"] if seeds is None else seeds
    return result


def scoped_base() -> RevisionState:
    return state(node("r", mutable=True, members={"children": ["a", "x"]}),
        node("a", members={"children": ["b"]}), node("b"), node("x", uses=["a"]),
        publications=[{"kind": "prompt", "publication_id": "px", "name": "external",
            "description": "external", "opens_id": "x", "model_may_open": True}],
        presentations={"default_locale": "en", "catalogs": [{"locale": "en", "entries": [
            {"node_id": "x", "title": "outside"}, {"node_id": "a", "title": "old"}]}]})


@pytest.mark.parametrize("op", ["replace_subtree", "import", "reconcile"])
def test_partial_subtree_all_surfaces_exact_mapping_replay_and_reconcile_noop(op: str) -> None:
    base = scoped_base()
    target: Any = base.definition.as_json()
    nodes = {n["node_id"]: n for n in target["nodes"]}
    nodes["a"]["members"]["children"] = ["c"]
    nodes["a"]["uses"] = ["x"]
    nodes["a"].pop("instructions")
    nodes["a"]["instruction_template"] = {"parts": [{"kind": "NodeRef", "target_id": "x"}]}
    target["nodes"] = [n for n in nodes.values() if n["node_id"] != "b"] + [node("c")]
    target["publications"].append({"kind": "prompt", "publication_id": "pa", "name": "inside",
        "description": "inside", "opens_id": "a", "model_may_open": True})
    target["presentations"]["catalogs"][0]["entries"][1]["title"] = "inside"
    target["presentations"]["catalogs"].append({"locale": "fr", "entries": [{"node_id": "c", "title": "C"}]})
    prepared = build(base, scoped_command(op, target, new=["c"], deleted=["b"]))
    result = roundtrip(prepared)
    assert result.definition.nodes["x"] == base.definition.nodes["x"]
    assert result.definition.nodes["r"] == base.definition.nodes["r"]
    assert [t["node_id"] for t in result.tombstones] == ["b"]
    assert set(prepared.protection.reference_targets) >= {"x"}
    noop = build(result, scoped_command("reconcile", target, retained=["a", "c"]))
    assert noop.change.noop and noop.change.tombstones == result.tombstones
    assert roundtrip(noop) == result
    assert base.definition.nodes["a"].members is not None
    assert "b" in base.definition.nodes and "c" not in base.definition.nodes


@pytest.mark.parametrize("op", ["replace_subtree", "import", "reconcile"])
@pytest.mark.parametrize("tamper", ["node", "uses", "template", "placement", "capture", "pub", "locale", "default", "metadata", "mapping"])
def test_partial_scope_escape_or_tampered_mapping_atomic_failure(op: str, tamper: str) -> None:
    base = scoped_base()
    original = canonical_json(base.definition.as_json())
    target: Any = base.definition.as_json()
    nodes = {n["node_id"]: n for n in target["nodes"]}
    if tamper == "node":
        nodes["x"]["description"] = "escape"
    elif tamper == "uses":
        nodes["x"]["uses"] = ["b"]
    elif tamper == "template":
        nodes["x"].pop("instructions")
        nodes["x"]["instruction_template"] = {"parts": [{"kind": "NodeRef", "target_id": "b"}]}
    elif tamper == "placement":
        nodes["r"]["members"]["children"] = ["a"]
        target["roots"].append({"node_id": "x", "plane": "prompt"})
    elif tamper == "capture":
        nodes["r"]["members"]["children"] = ["a"]
        nodes["a"]["members"]["children"].append("x")
    elif tamper == "pub":
        target["publications"][0]["opens_id"] = "a"
    elif tamper == "locale":
        target["presentations"]["catalogs"][0]["entries"][0]["title"] = "escape"
    elif tamper == "default":
        target["presentations"]["default_locale"] = "fr"
    elif tamper == "metadata":
        target["extensions"] = {"test:escape": True}
    command = scoped_command(op, target, retained=["a", "b"])
    if tamper == "mapping":
        command["id_mapping"]["retained_ids"].append("x")
    with pytest.raises(GraphError):
        build(base, command)
    assert canonical_json(base.definition.as_json()) == original
    assert base.tombstones == () and base.revision == "1"


@pytest.mark.parametrize("op", ["replace_subtree", "import", "reconcile"])
def test_partial_target_placement_and_incoming_reference_closure(op: str) -> None:
    base = scoped_base()
    target: Any = base.definition.as_json()
    nodes = {n["node_id"]: n for n in target["nodes"]}
    nodes["r"]["members"]["children"] = ["x"]
    nodes["x"]["members"]["children"] = ["a"]
    prepared = build(base, scoped_command(op, target, retained=["a", "b"]))
    assert roundtrip(prepared).definition.nodes["x"].members.children == ("a",)
    # Deleting a target cannot silently repair an out-of-scope uses source.
    target["nodes"] = [n for n in target["nodes"] if n["node_id"] not in ("a", "b")]
    nodes["x"]["uses"] = []
    nodes["x"]["members"]["children"] = []
    target["presentations"]["catalogs"][0]["entries"].pop()
    if op != "replace_subtree":
        with pytest.raises(GraphError, match="outside node|boundary reference"):
            build(base, scoped_command(op, target, retained=[], deleted=["a", "b"]))


def test_import_new_anchor_and_multiple_scope_anchors() -> None:
    base = scoped_base()
    target: Any = base.definition.as_json()
    target["nodes"].append(node("new"))
    next(n for n in target["nodes"] if n["node_id"] == "r")["members"]["children"].append("new")
    result = roundtrip(build(base, scoped_command("import", target, seeds=["new"], retained=[], new=["new"])))
    assert "new" in result.definition.nodes
    target = result.definition.as_json()
    for n in target["nodes"]:
        if n["node_id"] in ("a", "x"):
            n["description"] = "both"
    assert not build(result, scoped_command("reconcile", target, seeds=["a", "x"],
        retained=["a", "b", "x"])).change.noop


def test_reconcile_cannot_reset_policy_or_reuse_tombstone_identity() -> None:
    base = scoped_base()
    target: Any = base.definition.as_json()
    next(n for n in target["nodes"] if n["node_id"] == "a")["mutable"] = True
    with pytest.raises(GraphError, match="creation identity"):
        build(base, scoped_command("reconcile", target, retained=["a", "b"]))
    tombstone = {"node_id": "gone", "kind": "role", "mutable": "inherit", "deleted_revision": "1"}
    current = replace(base, tombstones=(tombstone,))
    target = base.definition.as_json()
    target["nodes"].append(node("gone"))
    next(n for n in target["nodes"] if n["node_id"] == "a")["members"]["children"].append("gone")
    with pytest.raises(GraphError, match="resurrect"):
        build(current, scoped_command("reconcile", target, retained=["a", "b"], new=["gone"]))


def test_prepare_revert_scoped_preserves_concurrent_work_and_rechecks_history() -> None:
    initial = scoped_base()
    renamed = build(initial, {"op": "rename_node", "node_id": "a", "new_name": "new"},
        {"op": "update_node", "node_id": "x", "set_fields": {"description": "concurrent"}, "unset_fields": []})
    current = roundtrip(renamed)
    history = History.seed(initial, journal_seq="1").append(change_event(initial, renamed.change,
        command_digest=renamed.envelope.command_digest, reason="test"), journal_seq="2", tombstones=[])
    prepared = prepare_revert(current, "1", node_ids=("a",), context=CTX, admission=Allow(), history=history,
        idempotency_key="revert", reason="undo name", correlation_id="trace")
    receipt = preview(prepared, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
    admitted = admit_transition(receipt, current, context=CTX, admission=Allow(), now=NOW, history=history)
    assert admitted.status == "admitted" and admitted.result_revision == "3"
    assert admitted.change.candidate.nodes["a"].name == "a"
    assert admitted.change.candidate.nodes["x"].description == "concurrent"
    assert current.definition.nodes["a"].name == "new"
    assert not hasattr(admitted, "journal_seq")
    with pytest.raises(GraphError, match="provider"):
        admit_transition(receipt, current, context=CTX, admission=Allow(), now=NOW)
    denied = Allow()
    denied.deny = True
    with pytest.raises(GraphError):
        admit_transition(receipt, current, context=CTX, admission=denied, now=NOW, history=history)


def test_prepare_revert_history_backed_restore_retains_current_tombstones() -> None:
    initial = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))
    deleted = build(initial, {"op": "delete_nodes", "targets": ["a"], "deleted_ids": ["a"],
        "policy": "reject_references", "disconnections": [{"source_id": "r", "target_id": "a", "relation": "containment"}]})
    current = roundtrip(deleted)
    history = History.seed(initial, journal_seq="1").append(change_event(initial, deleted.change,
        command_digest=deleted.envelope.command_digest, reason="delete"), journal_seq="2", tombstones=deleted.change.tombstones)
    prepared = prepare_revert(current, "1", context=CTX, admission=Allow(), history=history,
        idempotency_key="restore", reason="undo delete", correlation_id="trace")
    assert prepared.change.tombstones == current.tombstones
    assert prepared.change.facts[0]["type"] == "NodeRestored"
    receipt = preview(prepared, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
    admitted = admit_transition(receipt, current, context=CTX, admission=Allow(), now=NOW, history=history)
    replayed = apply_facts(current, admitted.change.facts, tombstones=admitted.change.tombstones,
        revision="3", before_digest=current.definition_digest, after_digest=initial.definition_digest)
    assert replayed.definition == initial.definition and replayed.tombstones == current.tombstones


@pytest.mark.parametrize("op", ["replace_subtree", "import", "reconcile"])
def test_scoped_deletion_and_exact_boundary_repair_replay(op: str) -> None:
    base = state(node("r", mutable=True, members={"children": ["a", "x"]}),
        node("a", members={"children": ["b"]}), node("b"), node("x"))
    target: Any = base.definition.as_json()
    target["nodes"] = [n for n in target["nodes"] if n["node_id"] not in ("a", "b")]
    next(n for n in target["nodes"] if n["node_id"] == "r")["members"]["children"] = ["x"]
    prepared = build(base, scoped_command(op, target, retained=[], deleted=["a", "b"]))
    result = roundtrip(prepared)
    assert set(result.definition.nodes) == {"r", "x"}
    assert [t["node_id"] for t in result.tombstones] == ["a", "b"]
    # Exact incoming repairs must be explicitly owned, not inferred cascades.
    references = scoped_base()
    target = references.definition.as_json()
    for n in target["nodes"]:
        if n["node_id"] == "a":
            n["members"]["children"] = []
        if n["node_id"] == "x":
            n["uses"] = []
    target["nodes"] = [n for n in target["nodes"] if n["node_id"] != "b"]
    if op != "replace_subtree":
        result = roundtrip(build(references, scoped_command(op, target, seeds=["a", "x"],
            retained=["a", "x"], deleted=["b"])))
        assert result.definition.nodes["x"].uses == ()


def test_scoped_blob_exact_bytes_and_mapping_rechecked_at_admission() -> None:
    base = scoped_base()
    target: Any = base.definition.as_json()
    next(n for n in target["nodes"] if n["node_id"] == "a")["description"] = "blob"
    data = canonical_json(target)
    command = scoped_command("reconcile", target, retained=["a", "b"])
    command["payload"] = {"blob": {"digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "size": str(len(data)), "media_type": "application/json"}}

    class Payload:
        def read(self, reference: Any) -> bytes:
            return data

    prepared = build(base, command, payloads=Payload())
    receipt = preview(prepared, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
    admitted = admit_transition(receipt, base, context=CTX, admission=Allow(), now=NOW, payloads=Payload())
    assert admitted.change.candidate.nodes["a"].description == "blob"
    forged = dict(command)
    forged["id_mapping"] = {"retained_ids": ["a", "b", "x"], "new_ids": [], "deleted_ids": []}
    invalid = TransactionEnvelope.from_json(envelope(base, forged))
    with pytest.raises(GraphError, match="partition"):
        admit_transition(replace(receipt, prepared=replace(prepared, envelope=invalid)), base,
            context=CTX, admission=Allow(), now=NOW, payloads=Payload())


def test_aggregate_catalog_order_empty_entries_and_tampered_facts_replay() -> None:
    base = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))
    target: Any = base.definition.as_json()
    target["presentations"] = {"default_locale": "fr", "catalogs": [
        {"locale": "fr", "entries": [{"node_id": "a", "title": "A"}, {"node_id": "r", "title": "R"}]},
        {"locale": "en", "entries": []}]}
    prepared = build(base, scoped_command("import", target, seeds=["r"], retained=["r", "a"]))
    assert roundtrip(prepared).definition.presentations.as_json() == target["presentations"]
    from contexture.core.graph.canonical import thaw_json

    facts: Any = thaw_json(prepared.change.facts)
    facts[0]["after"]["catalogs"].reverse()
    with pytest.raises(GraphError):
        apply_facts(base, facts, tombstones=[], revision="2", before_digest=base.definition_digest,
            after_digest=prepared.change.candidate.digest(), resolved_fact_digest=prepared.change.facts_digest)


def test_ordinary_payload_cannot_rename_application_or_change_schema_identity() -> None:
    base = state()
    for key, value in [("application_name", "other"), ("graph_id", "other"), ("semantic_version", "2")]:
        target: Any = base.definition.as_json()
        target[key] = value
        with pytest.raises(GraphError):
            build(base, scoped_command("import", target, seeds=["r"], retained=["r"]))