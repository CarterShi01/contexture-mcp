"""Dynamic-tree mutability semantics (M1).

Policy, in agent-context terms:

* ``mutable=False`` is a barrier: the whole subtree is frozen.
* An effectively locked node (e.g. a root left at ``inherit``) protects its own
  definition, path, plane, publications/presentations and the presence and
  relative order of its locked members.
* Mutable members of a locked parent are fully editable: rename, describe,
  re-instruct, reorder (not across a locked sibling), move between non-barrier
  parents and delete. New direct members of a locked parent must be mutable, so
  ordinary mutation never creates something nobody may remove.
* When a mutable member/use/template target changes, locked nodes whose
  reachable agent context changes are reported as ``protected_dependents`` and
  included in admission impact; the change is not refused.
* Root promotion/demotion is host-gated bootstrap authority and may re-derive
  inherited mutability; ordinary moves may not flip an existing node's
  effective mutability.
"""

from __future__ import annotations

from typing import Any

import pytest

from contexture.core.graph.errors import GraphError
from contexture.core.graph.transactions import preview
from tests.test_managed_transactions import CTX, LATER, NOW, Allow, build, node, roundtrip, state


def skill(identity: str, **fields: Any) -> dict[str, Any]:
    return {"node_id": identity, "kind": "skill", "name": identity,
            "description": "description", "instructions": "program", **fields}


def denied(base: Any, *commands: dict[str, Any], rule: str, node_id: str) -> None:
    with pytest.raises(GraphError) as error:
        build(base, *commands)
    assert error.value.code == "mutation_protected", error.value
    assert [(d.code, d.node_id) for d in error.value.diagnostics] == [(rule, node_id)]


def attach(identity: str, parent: str, slot: str = "children", position: int = 0) -> dict[str, Any]:
    return {"op": "attach_node", "node_id": identity,
            "target": {"parent_id": parent, "slot": slot, "position": position}}


def forest() -> Any:
    """R (root, inherit => locked) holds mutable C (with G) and locked L;
    R2 is a second locked root; B is an explicit false barrier with true T."""
    return state(
        node("R", members={"children": ["C", "L", "D"]}),
        node("C", mutable=True, members={"children": ["G"]}),
        node("G"),
        node("L"),
        node("D", mutable=True),
        node("R2", members={"children": ["E"]}),
        node("E", mutable=True),
        node("B", mutable=False, members={"children": ["T"]}),
        node("T", mutable=True),
        roots=[{"node_id": "R", "plane": "model"}, {"node_id": "R2", "plane": "model"},
               {"node_id": "B", "plane": "model"}])


@pytest.mark.parametrize("command", [
    {"op": "rename_node", "node_id": "C", "new_name": "renamed"},
    {"op": "update_node", "node_id": "C", "set_fields": {"description": "new"}, "unset_fields": []},
    {"op": "update_node", "node_id": "C", "set_fields": {"instructions": "new"}, "unset_fields": []},
    {"op": "rename_node", "node_id": "G", "new_name": "renamed"},
], ids=["rename", "describe", "instruct", "grandchild"])
def test_mutable_child_of_locked_parent_is_editable(command: dict[str, Any]) -> None:
    prepared = build(forest(), command)
    roundtrip(prepared)
    if command["node_id"] == "C":
        # R's agent-visible member card/definition changed; R itself did not.
        assert prepared.protection.protected_dependents == ("R",)
        assert {"C", "R"} <= set(prepared.protection.impact)
    else:
        # G's direct container C is mutable, so no locked dependent is involved.
        assert prepared.protection.protected_dependents == ()


def test_mutable_child_of_locked_parent_can_be_deleted_with_its_subtree() -> None:
    result = roundtrip(build(forest(), {"op": "delete_nodes", "targets": ["C"], "deleted_ids": ["C", "G"],
        "policy": "explicit_cascade", "disconnections": [
            {"source_id": "R", "target_id": "C", "relation": "containment"}]}))
    members = result.definition.nodes["R"].members
    assert members is not None and members.children == ("L", "D")
    assert {"C", "G"} <= {str(t["node_id"]) for t in result.tombstones}


def test_locked_parent_accepts_new_mutable_member_but_not_inherited_lock() -> None:
    base = forest()
    result = roundtrip(build(base, {"op": "create_node", "node": skill("k", mutable=True)},
                             attach("k", "R", "skills")))
    assert result.definition.nodes["R"].members.skills == ("k",)  # type: ignore[union-attr]
    # A new subtree under the new mutable member inherits mutability.
    roundtrip(build(base, {"op": "create_node", "node": node("N", mutable=True)},
        {"op": "create_node", "node": skill("s")}, attach("N", "R", position=3),
        attach("s", "N", "skills")))
    denied(base, {"op": "create_node", "node": skill("k")}, attach("k", "R", "skills"),
           rule="locked_create", node_id="k")
    denied(base, {"op": "create_node", "node": skill("k", mutable=False)}, attach("k", "R", "skills"),
           rule="locked_create", node_id="k")


def test_mutable_members_reorder_but_cannot_cross_locked_sibling() -> None:
    base = forest()
    # C and D are both mutable; swapping them crosses locked L.
    denied(base, {"op": "reorder_members", "parent_id": "R", "slot": "children",
                  "node_ids": ["D", "L", "C"]}, rule="locked_order", node_id="L")
    tree = state(node("R", members={"children": ["C", "D", "L"]}),
                 node("C", mutable=True), node("D", mutable=True), node("L"),
                 roots=[{"node_id": "R", "plane": "model"}])
    prepared = build(tree, {"op": "reorder_members", "parent_id": "R", "slot": "children",
                            "node_ids": ["D", "C", "L"]})
    assert {"C", "D"} <= set(prepared.protection.impact)
    roundtrip(prepared)


def test_mutable_member_moves_between_locked_parents() -> None:
    result = roundtrip(build(forest(), {"op": "move_node", "node_id": "C",
        "target": {"parent_id": "R2", "slot": "children", "position": 1}}))
    assert result.definition.nodes["R2"].members.children == ("E", "C")  # type: ignore[union-attr]
    assert result.definition.nodes["G"].name == "G"


def test_move_cannot_lock_an_existing_mutable_node() -> None:
    # G inherits mutability from C; under locked R it would become locked.
    denied(forest(), {"op": "move_node", "node_id": "G",
        "target": {"parent_id": "R", "slot": "children", "position": 0}},
        rule="effective_mutability", node_id="G")


@pytest.mark.parametrize("command,rule,target", [
    ({"op": "rename_node", "node_id": "L", "new_name": "x"}, "locked_surface", "L"),
    ({"op": "update_node", "node_id": "R", "set_fields": {"description": "x"}, "unset_fields": []},
     "locked_surface", "R"),
    ({"op": "rename_node", "node_id": "T", "new_name": "x"}, "locked_surface", "T"),
    ({"op": "move_node", "node_id": "L", "target": {"parent_id": "R2", "slot": "children", "position": 0}},
     "locked_surface", "L"),
    ({"op": "move_node", "node_id": "C", "target": {"parent_id": "B", "slot": "children", "position": 0}},
     "barrier_move", "C"),
    ({"op": "put_presentation", "locale": "en", "presentation": {"node_id": "L", "title": "x"}},
     "locked_surface", "L"),
], ids=["rename-locked", "describe-locked-parent", "true-under-barrier", "move-locked",
        "move-into-barrier", "present-locked"])
def test_locked_and_barrier_objects_stay_protected(command: dict[str, Any], rule: str, target: str) -> None:
    denied(forest(), command, rule=rule, node_id=target)


def test_locked_member_deletion_and_barrier_creation_refused() -> None:
    base = forest()
    denied(base, {"op": "delete_nodes", "targets": ["L"], "deleted_ids": ["L"],
        "policy": "explicit_cascade", "disconnections": [
            {"source_id": "R", "target_id": "L", "relation": "containment"}]},
        rule="locked_deletion", node_id="L")
    denied(base, {"op": "create_node", "node": node("N", mutable=True)}, attach("N", "B"),
           rule="barrier_create", node_id="N")
    denied(base, {"op": "create_node", "node": node("N", mutable=True)}, attach("N", "T"),
           rule="barrier_create", node_id="N")


def test_locked_use_and_template_targets_report_dependents() -> None:
    base = state(node("R", members={"children": ["U", "V"]}, uses=["U"]),
        node("U", mutable=True),
        node("V", mutable=True),
        {**{k: v for k, v in node("P", uses=["V"]).items() if k != "instructions"},
         "instruction_template": {"parts": [{"kind": "Text", "text": "see "},
                                            {"kind": "NodeRef", "target_id": "V"}]}},
        roots=[{"node_id": "R", "plane": "model"}, {"node_id": "P", "plane": "model"}])
    for command, expected in (
            ({"op": "rename_node", "node_id": "V", "new_name": "x"}, ("P", "R")),
            ({"op": "update_node", "node_id": "U", "set_fields": {"instructions": "x"},
              "unset_fields": []}, ("R",)),
            ({"op": "put_publication", "publication": {"kind": "prompt", "publication_id": "p",
              "name": "p", "description": "d", "opens_id": "U", "model_may_open": False}}, ("R",))):
        prepared = build(base, command)
        assert prepared.protection.protected_dependents == expected
        receipt = preview(prepared, context=CTX, admission=Allow(), now=NOW, expires_at=LATER)
        assert receipt.as_json()["protected_dependents"] == list(expected)
        roundtrip(prepared)


def test_admission_must_cover_protected_dependents() -> None:
    from contexture.core.graph.policy import AdmissionDecision

    class WithoutDependents(Allow):
        def decide(self, request: Any) -> AdmissionDecision:
            return AdmissionDecision(True, "1", frozenset(
                set(request.impact) - {"R"} | set(request.reference_targets)))

    prepared = build(forest(), {"op": "rename_node", "node_id": "C", "new_name": "x"})
    with pytest.raises(GraphError) as error:
        preview(prepared, context=CTX, admission=WithoutDependents(), now=NOW, expires_at=LATER)
    assert error.value.code == "permission_denied"


def test_root_promotion_is_host_authority_and_may_lock_subtree() -> None:
    base = state(node("R", mutable=True, members={"children": ["A"]}), node("A", members={"children": ["Z"]}),
                 node("Z"), roots=[{"node_id": "R", "plane": "model"}])
    prepared = build(base, {"op": "move_node", "node_id": "A", "target": {"plane": "model", "position": 1}})
    assert prepared.protection.roots_changed
    result = roundtrip(prepared)
    assert [r.node_id for r in result.definition.roots] == ["R", "A"]
