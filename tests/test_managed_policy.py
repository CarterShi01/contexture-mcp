"""Protection regression cases exercise the same mutation gate as replace."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from contexture.core.graph.errors import GraphError
from contexture.core.graph.policy import AdmissionDecision, VerifiedContext, read_snapshot
from contexture.core.graph.transactions import admit_transition, prepare, prepare_revert, preview
from tests.test_managed_transactions import (
    CTX, LATER, NOW, Allow, build, envelope, node, roundtrip, scoped_command, state,
)


@pytest.mark.parametrize("op", ["replace_subtree", "import", "reconcile"])
def test_partial_payload_mutable_use_target_change_reports_locked_dependent(op: str) -> None:
    # A locked node protects its own definition, not the mutable nodes it uses:
    # renaming a mutable use target is allowed, and the locked user is reported.
    base = state(node("r", mutable=True, members={"children": ["a", "locked"]}),
        node("a"), node("locked", mutable=False, uses=["a"]))
    target: Any = base.definition.as_json()
    next(n for n in target["nodes"] if n["node_id"] == "a")["name"] = "renamed"
    prepared = build(base, scoped_command(op, target))
    assert prepared.protection.protected_dependents == ("locked",)
    assert {"a", "locked"} <= set(prepared.protection.impact)
    assert roundtrip(prepared).definition.nodes["a"].name == "renamed"
    assert base.definition.nodes["a"].name == "a" and base.tombstones == ()


@pytest.mark.parametrize("op", ["replace_subtree", "import", "reconcile"])
def test_partial_payload_protected_target_placement_atomic_fail(op: str) -> None:
    base = state(node("r", mutable=True, members={"children": ["a", "locked"]}),
        node("a"), node("locked", mutable=False))
    target: Any = base.definition.as_json()
    nodes = {n["node_id"]: n for n in target["nodes"]}
    nodes["r"]["members"]["children"] = ["locked"]
    nodes["locked"]["members"]["children"] = ["a"]
    protected(scoped_command(op, target), base=base)


def test_complete_default_locale_change_locked_surface_atomic_fail() -> None:
    base = state(node("r", mutable=True, members={"children": ["locked"]}), node("locked", mutable=False))
    target: Any = base.definition.as_json()
    target["presentations"]["default_locale"] = "fr"
    protected(scoped_command("import", target, seeds=["r"], retained=["r", "locked"]), base=base)
    assert base.definition.presentations.default_locale == "en"


@pytest.mark.parametrize("field,value", [("extensions", {"test:execution": "new"}), ("mode", "runtime")])
def test_whole_scope_graph_metadata_cannot_bypass_locked_surface(field: str, value: Any) -> None:
    base = state(node("r", mutable=True, members={"children": ["locked"]}), node("locked", mutable=False))
    target: Any = base.definition.as_json()
    target[field] = value
    protected(scoped_command("reconcile", target, seeds=["r"], retained=["r", "locked"]), base=base)


def test_aggregate_publication_and_presentation_order_protection_and_impact() -> None:
    publications = [{"kind": "prompt", "publication_id": p, "name": p, "description": p,
        "opens_id": n, "model_may_open": True} for p, n in [("pa", "a"), ("pl", "locked")]]
    base = state(node("r", mutable=True, members={"children": ["a", "locked"]}), node("a"),
        node("locked", mutable=False), publications=publications,
        presentations={"default_locale": "en", "catalogs": [{"locale": "en", "entries": [
            {"node_id": "a", "title": "A"}, {"node_id": "locked", "title": "L"}]}]})
    for field in ("publications", "entries"):
        target: Any = base.definition.as_json()
        if field == "publications":
            target["publications"].reverse()
        else:
            target["presentations"]["catalogs"][0]["entries"].reverse()
        protected(scoped_command("reconcile", target, seeds=["r"], retained=["r", "a", "locked"]), base=base)


def test_revert_uses_current_protected_reference_surface_not_historical_permission() -> None:
    from contexture.core.graph.history import History

    historical = state(node("r", mutable=True, members={"children": ["a", "locked"]}),
        node("a", name="old"), node("locked", mutable=False, uses=["a"]))
    current = state(node("r", mutable=True, members={"children": ["a", "locked"]}),
        node("a", name="current"), node("locked", mutable=False, uses=["a"]), revision="2")
    prepared = prepare_revert(current, "1", node_ids=("a",), context=CTX, admission=Allow(),
        history=History.seed(historical, journal_seq="1"), idempotency_key="undo",
        reason="test", correlation_id="trace")
    # Revert is judged against the CURRENT protection surface: the locked user
    # is an affected dependent needing admission, not a historical permission.
    assert prepared.protection.protected_dependents == ("locked",)
    assert prepared.change.candidate.nodes["a"].name == "old"
    assert current.definition.nodes["a"].name == "current"
    barrier = state(node("r", mutable=False, members={"children": ["a"]}), node("a", name="current"),
                    revision="2")
    with pytest.raises(GraphError) as error:
        prepare_revert(barrier, "1", node_ids=("a",), context=CTX, admission=Allow(),
            history=History.seed(state(node("r", mutable=False, members={"children": ["a"]}),
                node("a", name="old")), journal_seq="1"), idempotency_key="undo",
            reason="test", correlation_id="trace")
    assert error.value.code == "mutation_protected"


def protected(*commands: dict[str, Any], base: Any) -> None:
    with pytest.raises(GraphError) as error:
        build(base, *commands)
    assert error.value.code == "mutation_protected"


def test_locked_create_false_barrier_and_creation_policy_immutable() -> None:
    base = state(node("r", mutable=True, members={"children": ["locked"]}),
                 node("locked", mutable=False))
    protected({"op": "create_node", "node": node("a", mutable=True)},
        {"op": "attach_node", "node_id": "a", "target": {
            "parent_id": "locked", "slot": "children", "position": 0}}, base=base)
    protected({"op": "rename_node", "node_id": "locked", "new_name": "new"}, base=base)
    protected({"op": "update_node", "node_id": "locked", "set_fields": {
        "instructions": "new"}, "unset_fields": []}, base=base)
    # A true descendant cannot escape an explicit false ancestor.
    barrier = state(node("r", mutable=False, members={"children": ["child"]}),
                    node("child", mutable=True))
    protected({"op": "rename_node", "node_id": "child", "new_name": "new"}, base=barrier)
    # Default root inheritance is false, never an implicit root grant.
    protected({"op": "rename_node", "node_id": "r", "new_name": "new"}, base=state(node("r")))


def test_locked_relative_order_allows_insert_but_not_crossing() -> None:
    base = state(node("r", mutable=True, members={"children": ["a", "locked", "b"]}),
                 node("a"), node("locked", mutable=False), node("b"))
    inserted = roundtrip(build(base, {"op": "create_node", "node": node("new")},
        {"op": "attach_node", "node_id": "new", "target": {
            "parent_id": "r", "slot": "children", "position": 0}}))
    members = inserted.definition.nodes["r"].members
    assert members is not None and members.children == ("new", "a", "locked", "b")
    protected({"op": "reorder_members", "parent_id": "r", "slot": "children",
               "node_ids": ["locked", "a", "b"]}, base=base)
    protected({"op": "move_node", "node_id": "a", "target": {
        "parent_id": "r", "slot": "children", "position": 2}}, base=base)


def test_reorder_impact_includes_both_existing_inversion_endpoints() -> None:
    base = state(node("r", mutable=True, members={"children": ["a", "b", "c"]}),
                 node("a"), node("b"), node("c"))
    prepared = build(base, {"op": "reorder_members", "parent_id": "r", "slot": "children",
                           "node_ids": ["b", "a", "c"]})
    assert set(prepared.protection.impact) == {"r", "a", "b"}


def test_locked_subtree_delete_and_ancestor_rename_move_plane() -> None:
    base = state(node("r", mutable=True, members={"children": ["a", "other"]}),
        node("a", members={"children": ["locked"]}), node("locked", mutable=False), node("other"))
    protected({"op": "delete_nodes", "targets": ["a"], "deleted_ids": ["a", "locked"],
        "policy": "explicit_cascade", "disconnections": [
            {"source_id": "r", "target_id": "a", "relation": "containment"}]}, base=base)
    protected({"op": "rename_node", "node_id": "a", "new_name": "renamed"}, base=base)
    protected({"op": "move_node", "node_id": "a", "target": {
        "parent_id": "other", "slot": "children", "position": 0}}, base=base)
    protected({"op": "move_node", "node_id": "a", "target": {"plane": "prompt", "position": 1}}, base=base)
    # No fictional inherited ancestor instructions: this does not change locked child program.
    result = roundtrip(build(base, {"op": "update_node", "node_id": "a",
        "set_fields": {"instructions": "new owner program"}, "unset_fields": []}))
    assert result.definition.nodes["locked"].instructions == "program"


def test_locked_publication_presentation_and_reference_surface() -> None:
    base = state(node("r", mutable=True, members={"children": ["locked", "a"]}),
        node("locked", mutable=False, uses=["a"]), node("a"))
    protected({"op": "put_presentation", "locale": "en", "presentation": {
        "node_id": "locked", "title": "new"}}, base=base)
    protected({"op": "put_publication", "publication": {"kind": "prompt", "publication_id": "p",
        "name": "entry", "description": "entry", "opens_id": "locked", "model_may_open": False}}, base=base)
    for command in ({"op": "rename_node", "node_id": "a", "new_name": "new"},
                    {"op": "move_node", "node_id": "a", "target": {"plane": "prompt", "position": 1}}):
        prepared = build(base, command)
        assert prepared.protection.protected_dependents == ("locked",)
        roundtrip(prepared)


@pytest.mark.parametrize("op", ["replace_subtree", "import", "reconcile"])
def test_replace_paths_cannot_bypass_protection_or_identity(op: str) -> None:
    base = state(node("r", mutable=True, members={"children": ["locked"]}), node("locked", mutable=False))
    target: Any = base.definition.as_json()
    target["nodes"][0]["description"] = "tampered"  # locked sorts before r
    cmd = {"op": op, "id_mapping": {"retained_ids": ["r", "locked"], "new_ids": [], "deleted_ids": []},
           "payload": {"inline": target}}
    cmd["anchor_id" if op == "replace_subtree" else "scope_ids"] = "r" if op == "replace_subtree" else ["r", "locked"]
    protected(cmd, base=base)
    target["nodes"][0]["mutable"] = True
    with pytest.raises(GraphError) as error:
        build(base, cmd)
    assert error.value.code == "identity_conflict"


def test_root_operations_require_host_permission_not_model_arguments() -> None:
    base = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))
    request = envelope(base, {"op": "move_node", "node_id": "a", "target": {"plane": "prompt", "position": 1}})
    ctx = VerifiedContext("actor", "tenant", "1")
    with pytest.raises(GraphError) as error:
        prepare(base, request, context=ctx, admission=Allow())
    assert error.value.code == "permission_denied"
    with pytest.raises(GraphError):
        prepare(base, {**request, "host_permissions": ["graph.roots"]}, context=ctx, admission=Allow())
    assert build(base, *request["commands"]).protection.roots_changed


def test_read_and_operation_admission_scope_and_reference_target_are_separate() -> None:
    base = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))

    class Restricted(Allow):
        def decide(self, request: Any) -> AdmissionDecision:
            if request.operation == "graph.read":
                return super().decide(request)
            return AdmissionDecision(True, "1", frozenset({"r"}))

    prepared = build(base, {"op": "add_use", "source_id": "r", "target_id": "a"})
    assert "a" in prepared.protection.reference_targets
    with pytest.raises(GraphError) as error:
        preview(prepared, context=CTX, admission=Restricted(), now=NOW, expires_at=LATER)
    assert error.value.code == "permission_denied"
    deny = Allow()
    deny.deny = True
    with pytest.raises(GraphError):
        read_snapshot(base, context=CTX, admission=deny)
    with pytest.raises(GraphError):
        preview(prepared, context=replace(CTX, actor_id="other"), admission=Allow(), now=NOW, expires_at=LATER)
    receipt = preview(prepared, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
    with pytest.raises(GraphError):
        admit_transition(receipt, base, context=CTX, admission=deny, now=NOW)