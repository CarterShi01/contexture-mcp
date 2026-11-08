"""Framework-level export/import and three-way merge (M7).

Business-neutral: bundles carry only graph objects; imports and merges are
ordinary previewed, admitted transactions; executable parts are binding
references the target registry must vouch for.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from contexture.core.graph.bundles import GraphBundle, HistoryBundle, export_bundle, plan_import
from contexture.core.graph.errors import GraphError
from contexture.core.graph.merge import merge
from contexture.core.graph.model import GraphDefinition
from contexture.server.managed_host import ManagedGraphHost, SingleActorPolicy
from tests.test_managed_transactions import build, node, roundtrip, state


def skill(identity: str, **fields: Any) -> dict[str, Any]:
    return {"node_id": identity, "kind": "skill", "name": identity, "description": identity,
            "instructions": "do " + identity, "mutable": True, **fields}


def library() -> Any:
    """A source graph: a mutable 'area' with a Skill using an outside node,
    a templated Skill, a nested Role, a publication and a translation."""
    return state(
        node("root", members={"children": ["area", "shared"]}),
        node("area", mutable=True, members={"children": ["sub"], "skills": ["s1", "s2"]}),
        node("sub", members={"skills": ["s3"]}),
        skill("s1", uses=["shared"]),
        {**{k: v for k, v in skill("s2").items() if k != "instructions"},
         "instruction_template": {"parts": [{"kind": "Text", "text": "after "},
                                            {"kind": "NodeRef", "target_id": "s1"}]}},
        skill("s3"),
        node("shared"),
        roots=[{"node_id": "root", "plane": "model"}],
        publications=[{"kind": "prompt", "publication_id": "p-s1", "name": "s1-entry",
                       "description": "entry", "opens_id": "s1", "model_may_open": True}],
        presentations={"default_locale": "en", "catalogs": [
            {"locale": "en", "entries": [{"node_id": "s1", "title": "Skill one"}]}]})


def target_graph() -> Any:
    return state(node("r", members={"children": ["dest", "shared"]}),
                 node("dest", mutable=True), node("shared"),
                 roots=[{"node_id": "r", "plane": "model"}])


def test_subtree_bundle_lists_external_refs_and_round_trips_bytes() -> None:
    bundle = export_bundle(library(), node_ids=["area"])
    value = bundle.as_json()
    assert value["anchors"] == ["area"] and value["external_refs"] == ["shared"]
    assert [n["node_id"] for n in value["nodes"]] == ["area", "sub", "s3", "s1", "s2"] or \
        {n["node_id"] for n in value["nodes"]} == {"area", "sub", "s1", "s2", "s3"}
    assert GraphBundle.from_json(bundle.canonical_bytes()).value == bundle.value
    tampered = json.loads(bundle.canonical_bytes())
    tampered["nodes"][0]["description"] = "evil"
    with pytest.raises(GraphError) as error:
        GraphBundle.from_json(tampered)
    assert error.value.code == "corrupt_facts"
    with pytest.raises(GraphError):
        export_bundle(library(), node_ids=["area", "sub"])  # overlapping anchors


def test_import_is_an_ordinary_transaction_preserving_ids() -> None:
    bundle = export_bundle(library(), node_ids=["area"])
    base = target_graph()
    commands = plan_import(base, bundle, target={"parent_id": "dest"})
    result = roundtrip(build(base, *commands))
    nodes = result.definition.nodes
    source = library().definition.nodes
    for identity in ("area", "sub", "s1", "s2", "s3"):
        assert nodes[identity].canonical_bytes() == source[identity].canonical_bytes()
    assert nodes["dest"].members.children == ("area",)  # type: ignore[union-attr]
    assert [p.publication_id for p in result.definition.publications] == ["p-s1"]
    assert result.definition.presentations.catalogs[0].entries[0].node_id == "s1"


def test_preserve_refuses_conflicts_and_remap_rewrites_every_reference() -> None:
    bundle = export_bundle(library(), node_ids=["area"])
    base = roundtrip(build(target_graph(), *plan_import(target_graph(), bundle, target={"parent_id": "dest"})))
    with pytest.raises(GraphError) as error:
        plan_import(base, bundle, target={"parent_id": "dest"})
    assert error.value.code == "identity_conflict"
    assert error.value.diagnostics[0].node_id == "area"
    with pytest.raises(GraphError):
        plan_import(base, bundle, target={"parent_id": "dest"}, prefix="copy-")  # needs identity=remap
    commands = plan_import(base, bundle, target={"parent_id": "r"}, identity="remap", prefix="copy-")
    copied = roundtrip(build(base, *commands)).definition
    assert copied.nodes["copy-s1"].uses == ("shared",)  # external ref kept
    template = copied.nodes["copy-s2"].instruction_template
    assert template is not None and template.parts[1].target_id == "copy-s1"  # type: ignore[union-attr]
    assert {p.publication_id for p in copied.publications} == {"p-s1", "copy-p-s1"}


def test_external_refs_must_resolve_and_locked_parents_require_mutable_members() -> None:
    bundle = export_bundle(library(), node_ids=["area"])
    lonely = state(node("r", members={"children": ["dest"]}), node("dest", mutable=True),
                   roots=[{"node_id": "r", "plane": "model"}])
    with pytest.raises(GraphError) as error:
        plan_import(lonely, bundle, target={"parent_id": "dest"})
    assert error.value.code == "reference_not_found"
    mapped = plan_import(lonely, bundle, target={"parent_id": "dest"}, external={"shared": "dest"})
    assert roundtrip(build(lonely, *mapped)).definition.nodes["s1"].uses == ("dest",)
    # Governance still applies: a locked parent only accepts mutable new members.
    locked = state(node("r", members={"children": ["shared"]}), node("shared"),
                   roots=[{"node_id": "r", "plane": "model"}])
    inherit = export_bundle(state(node("x", mutable=True, members={"children": ["y"]}), node("y"),
                                  roots=[{"node_id": "x", "plane": "model"}]), node_ids=["y"])
    with pytest.raises(GraphError) as error:
        build(locked, *plan_import(locked, inherit, target={"parent_id": "r"}))
    assert error.value.code == "mutation_protected"


def test_three_way_merge_field_level_lists_and_conflicts() -> None:
    base = library().definition

    def edit(change: Any) -> GraphDefinition:
        wire = base.as_json()
        change({n["node_id"]: n for n in wire["nodes"]}, wire)
        return GraphDefinition.from_json(wire)

    ours = edit(lambda n, w: n["s1"].update(description="ours"))
    theirs = edit(lambda n, w: (n["s3"].update(name="s3-renamed"), n["area"]["members"]["skills"].reverse()))
    result = merge(base, ours, theirs)
    assert result.clean and result.candidate is not None
    merged = result.candidate.nodes
    assert merged["s1"].description == "ours" and merged["s3"].name == "s3-renamed"
    assert merged["area"].members.skills == ("s2", "s1")  # type: ignore[union-attr]
    clash = edit(lambda n, w: n["s1"].update(description="theirs"))
    conflicted = merge(base, ours, clash)
    assert [c.key for c in conflicted.conflicts] == ["node:s1:description"]
    assert merge(base, ours, clash, resolutions={"node:s1:description": "theirs"}).candidate \
        .nodes["s1"].description == "theirs"  # type: ignore[union-attr]
    with pytest.raises(GraphError):
        merge(base, ours, theirs, resolutions={"node:nope:name": "ours"})


def test_merge_delete_modify_and_structural_conflicts() -> None:
    base = library().definition
    wire = base.as_json()
    nodes = {n["node_id"]: n for n in wire["nodes"]}
    nodes["sub"]["members"]["skills"] = []
    wire["nodes"] = [n for n in wire["nodes"] if n["node_id"] != "s3"]
    ours = GraphDefinition.from_json(wire)  # deleted s3
    wire = base.as_json()
    next(n for n in wire["nodes"] if n["node_id"] == "s3")["description"] = "edited"
    theirs = GraphDefinition.from_json(wire)
    conflicts = merge(base, ours, theirs).conflicts
    assert [(c.key, c.kind) for c in conflicts] == [("node:s3", "delete_modify")]
    # Both sides move s3 to different parents: each list merges cleanly, the
    # forest does not (s3 would have two parents) -> one structure conflict.
    def move(parent: str, create: bool) -> GraphDefinition:
        w = base.as_json()
        if create:
            w["nodes"].append(node(parent, mutable=True, members={"skills": []}))
            next(x for x in w["nodes"] if x["node_id"] == "root")["members"]["children"].append(parent)
        n = {x["node_id"]: x for x in w["nodes"]}
        n["sub"]["members"]["skills"] = []
        n[parent]["members"]["skills"].append("s3")
        return GraphDefinition.from_json(w)
    structural = merge(base, move("area", False), move("other", True))
    assert not structural.clean and [c.kind for c in structural.conflicts] == ["structure"]


@pytest_asyncio.fixture
async def hosts() -> AsyncIterator[tuple[ManagedGraphHost, ManagedGraphHost, Path]]:
    with tempfile.TemporaryDirectory(prefix="contexture-bundles-") as temp:
        async with ManagedGraphHost.open(Path(temp) / "a", graph_id="g", policy=SingleActorPolicy()) as a:
            async with ManagedGraphHost.open(Path(temp) / "b", graph_id="g", policy=SingleActorPolicy()) as b:
                yield a, b, Path(temp)


@pytest.mark.asyncio
async def test_host_export_bootstrap_merge_and_history_round_trip(hosts: Any) -> None:
    a, b, temp = hosts
    seed = library().definition.as_json()
    seed["graph_id"] = "g"
    a.bootstrap(seed)
    a.change({"op": "rename_node", "node_id": "s3", "new_name": "s3-v2"}, reason="edit")
    # Environment copy: seed B from A's head, edit both sides, merge back.
    exported = a.export_bundle()
    b.bootstrap_from(exported.canonical_bytes())
    assert b.read().definition_digest == a.read().definition_digest
    b.change({"op": "update_node", "node_id": "s1", "set_fields": {"description": "from staging"},
              "unset_fields": []}, reason="staging edit")
    a.change({"op": "update_node", "node_id": "s2", "set_fields": {"description": "from prod"},
              "unset_fields": []}, reason="prod edit")
    staged = b.export_bundle()
    # B's bundle descends from B's own revisions; merge needs A's base revision.
    result, receipt = a.merge(staged, base_revision="2", reason="merge staging")
    assert result.clean and receipt is not None
    head = a.read().definition
    assert head.nodes["s1"].description == "from staging" and head.nodes["s2"].description == "from prod"
    # Full history moves to a fresh store and replays byte-identically.
    history = a.export_history()
    assert HistoryBundle.from_json(history.canonical_bytes()).graph_id == "g"
    async with ManagedGraphHost.open(temp / "c", graph_id="g", policy=SingleActorPolicy()) as c:
        c.import_history(history.canonical_bytes())
        assert c.read().definition_digest == a.read().definition_digest
        assert len(c.history().records) == len(a.history().records)
        with pytest.raises(GraphError) as error:
            c.import_history(history)
        assert error.value.code == "revision_conflict"
    tampered = json.loads(history.canonical_bytes())
    tampered["events"][1]["reason"] = "rewritten"
    with pytest.raises(GraphError):
        HistoryBundle.from_json(tampered)


@pytest.mark.asyncio
async def test_host_subtree_import_preview_and_conflict(hosts: Any) -> None:
    a, b, _ = hosts
    source = library().definition.as_json()
    source["graph_id"] = "g"
    a.bootstrap(source)
    target = target_graph().definition.as_json()
    b.bootstrap(target)
    bundle = a.export_bundle(node_ids=["area"])
    proposal = b.import_bundle(bundle, target={"parent_id": "dest"}, reason="import", preview=True)
    assert set(proposal.as_json()["impact"]) >= {"area", "s1", "dest"}
    b.import_bundle(bundle.canonical_bytes(), target={"parent_id": "dest"}, reason="import")
    assert "s2" in b.read().definition.nodes
    with pytest.raises(GraphError) as error:
        b.import_bundle(bundle, target={"parent_id": "dest"}, reason="again")
    assert error.value.code == "identity_conflict"


@pytest.mark.asyncio
async def test_management_commands_plan_then_preview_and_commit(hosts: Any) -> None:
    from contexture.application.managed_management import ManagedManagementService

    a, b, _ = hosts
    source = library().definition.as_json()
    source["graph_id"] = "g"
    a.bootstrap(source)
    b.bootstrap(target_graph().definition.as_json())
    reader = ManagedManagementService(a.owner, mode="read_only")
    bundle = reader.execute("export", {"graph_id": "g", "node_ids": ["area"]})["result"]
    assert bundle["schema"] == "contexture.graph-bundle/v1"
    writer = ManagedManagementService(b.owner, mode="write")
    plan = writer.execute("plan-import", {"graph_id": "g", "bundle": bundle,
        "target": {"parent_id": "dest"}, "idempotency_key": "imp", "reason": "import",
        "correlation_id": "c"})["result"]
    preview = writer.execute("preview", {"transaction": plan["transaction"],
                                         "expires_at": "2099-01-01T00:00:00Z"})["result"]
    committed = writer.execute("commit", {"preview_id": preview["preview_id"]})
    assert committed["stage"] == "committed" and "s1" in b.read().definition.nodes
    history = reader.execute("export-history", {"graph_id": "g"})["result"]
    assert history["schema"] == "contexture.history-bundle/v1"
    with pytest.raises(GraphError):
        reader.execute("import-history", {"bundle": history, "approval_ref": "a", "correlation_id": "c"})
