"""contexture.directory/v1: a graph as plain Markdown/JSON files (R15).

Business-neutral: the codec knows only Roles, Skills and Tools; selecting what
to export is the application's choice; importing is an ordinary reconcile
transaction, so protection and admission still decide.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from contexture.core.graph.directory import (
    MANIFEST, encode_directory, escape_name, overlay_directory, parse_front_matter,
    render_front_matter, select_nodes, unescape_name,
)
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import GraphDefinition
from contexture.server.managed_host import ManagedGraphHost, SingleActorPolicy
from tests.test_managed_bundles import library, skill
from tests.test_managed_transactions import node, state


def ids(counter: list[int]) -> Any:
    def make() -> str:
        counter[0] += 1
        return f"new-{counter[0]}"
    return make


def tool(identity: str, **fields: Any) -> dict[str, Any]:
    return {"node_id": identity, "kind": "tool", "name": identity, "description": identity,
            "mutable": True, **fields}


def rich() -> GraphDefinition:
    """Every shape the codec must keep: process slots, variants, templates,
    tools, unsafe names, case-insensitive sibling clashes, translations."""
    return state(
        node("root", members={"children": ["area", "locked", "Area-2"], "pre_process": "pre"}),
        node("pre", role_variant="pre_process"),
        node("area", name="Area: one?", mutable=True, uses=["locked"],
             extensions={"oc:kind": "profile"},
             members={"children": ["sub"], "skills": ["s1", "s2"], "tools": ["t1"]}),
        node("Area-2", name="area: ONE?", mutable=True),
        node("sub", name="CON", instructions="line one\nline two\n"),
        skill("s1", uses=["locked"]),
        {**{k: v for k, v in skill("s2").items() if k != "instructions"},
         "instruction_template": {"parts": [{"kind": "Text", "text": "after "},
                                            {"kind": "NodeRef", "target_id": "s1", "label": "one"},
                                            {"kind": "Text", "text": " done"}]}},
        tool("t1", extensions={"oc:card": "structural"}),
        node("locked", mutable=False, instructions="system prompt, never exported"),
        roots=[{"node_id": "root", "plane": "model"}],
        presentations={"default_locale": "en", "catalogs": [
            {"locale": "en", "entries": [{"node_id": "s1", "title": "Skill one"}]},
            {"locale": "zh-CN", "entries": [{"node_id": "area", "title": "区域"}]}]},
    ).definition


def test_names_escape_reversibly_for_every_file_system() -> None:
    for name in ("plain", "a:b*c?", ".hidden", "trail. ", "CON", "con.txt", "100%", "x|y<z>\"\\"):
        escaped = escape_name(name)
        assert unescape_name(escaped) == name
        assert not set(escaped) & set(':*?"<>|\\') and not escaped.startswith(".")
        assert escaped.split(".")[0].lower() not in ("con", "prn", "aux", "nul")


def test_front_matter_is_a_json_valued_yaml_subset() -> None:
    raw = render_front_matter({"id": "x", "description": "a: b", "uses": ["u"]}, "body\n")
    assert raw.decode().startswith('---\nid: "x"\ndescription: "a: b"\nuses: ["u"]\n---\nbody\n')
    fields, body = parse_front_matter(raw, "f")
    assert fields == {"id": "x", "description": "a: b", "uses": ["u"]} and body == "body\n"
    # Hand-written YAML-style plain scalars and Windows line endings.
    fields, body = parse_front_matter(b"---\r\nname: my-skill\r\ndescription: Does it\r\n---\r\nhi\r\n", "f")
    assert fields == {"name": "my-skill", "description": "Does it"} and body == "hi"
    with pytest.raises(GraphError):
        parse_front_matter(b"---\nno separator\n---\n", "f")
    with pytest.raises(GraphError):
        parse_front_matter(b"---\nid: 1\n", "f")


def test_whole_graph_round_trips_losslessly() -> None:
    definition = rich()
    files = encode_directory(definition, revision="1")
    assert "root/ROLE.md" in files and "root/roles/Area%3A one%3F/ROLE.md" in files
    assert "root/roles/area%3A ONE%3F~2/ROLE.md" in files  # case-insensitive clash
    assert "root/roles/Area%3A one%3F/roles/%43ON/ROLE.md" in files
    assert "root/roles/Area%3A one%3F/skills/s1/SKILL.md" in files
    assert "root/roles/Area%3A one%3F/tools/t1.json" in files
    assert "root/roles/pre/ROLE.md" in files
    template = files["root/roles/Area%3A one%3F/skills/s2/SKILL.md"].decode()
    assert "\ntemplate: true\n" in template and template.endswith("---\nafter {{node:s1|one}} done\n")
    overlay = overlay_directory(definition, files)
    assert not overlay.changed and overlay.candidate.digest() == definition.digest()


def test_selection_exports_only_what_the_author_owns() -> None:
    seed = rich()
    wire = seed.as_json()
    for item in wire["nodes"]:
        if item["node_id"] == "s1":
            item["instructions"] = "edited by the author"
    current = GraphDefinition.from_json(wire)
    selected = select_nodes(current, mutable_only=True, changed_since=seed)
    assert selected == ("s1",)
    files = encode_directory(current, node_ids=selected, revision="2")
    manifest = json.loads(files[MANIFEST])
    assert manifest["nodes"] == ["s1"]
    assert manifest["placeholders"] == {"root": "root", "root/roles/Area%3A one%3F": "area"}
    assert sorted(files) == [MANIFEST, "root/roles/Area%3A one%3F/skills/s1/SKILL.md"]
    assert not any(b"system prompt" in raw for raw in files.values())
    assert set(select_nodes(current, mutable_only=True)) == {"area", "Area-2", "sub", "s1", "s2", "t1"}
    # Adding a member selects the member, not the parent whose content is unchanged.
    wire = current.as_json()
    wire["nodes"].append(skill("s9"))
    for item in wire["nodes"]:
        if item["node_id"] == "area":
            item["members"]["skills"].append("s9")
    grown = GraphDefinition.from_json(wire)
    assert select_nodes(grown, mutable_only=True, changed_since=seed) == ("s1", "s9")


def test_overlay_updates_creates_renames_reorders_and_deletes() -> None:
    definition = rich()
    files = dict(encode_directory(definition, node_ids=select_nodes(definition, mutable_only=True)))
    area = "root/roles/Area%3A one%3F"
    s1 = files.pop(f"{area}/skills/s1/SKILL.md").decode()
    files[f"{area}/skills/s1/SKILL.md"] = s1.replace("do s1", "do it better").encode()
    # Rename a role by renaming its folder; add a new skill without an id.
    sub = files.pop(f"{area}/roles/%43ON/ROLE.md")
    files[f"{area}/roles/renamed/ROLE.md"] = sub
    files[f"{area}/skills/fresh/SKILL.md"] = render_front_matter(
        {"name": "fresh", "description": "a new skill"}, "new instructions")
    # Reorder skills and delete the tool.
    role = files[f"{area}/ROLE.md"].decode().replace("ROLE", "ROLE")
    fields, body = parse_front_matter(role.encode(), "area")
    fields["order_skills"] = ["s2", "fresh", "s1"]
    files[f"{area}/ROLE.md"] = render_front_matter(fields, body)
    del files[f"{area}/tools/t1.json"]
    files["notes.txt"] = b"ignored"
    overlay = overlay_directory(definition, files, id_factory=ids([0]))
    nodes = overlay.candidate.nodes
    assert overlay.created == ("new-1",) and overlay.deleted == ("t1",)
    assert set(overlay.updated) == {"area", "s1", "sub"} and overlay.ignored == ("notes.txt",)
    assert nodes["s1"].instructions == "do it better" and nodes["sub"].name == "renamed"
    assert nodes["sub"].instructions == "line one\nline two\n"
    members = nodes["area"].members
    assert members is not None and members.skills == ("s2", "new-1", "s1") and members.tools == ()
    assert nodes["new-1"].kind == "skill" and nodes["new-1"].mutable == "inherit"


def test_overlay_moves_nodes_between_parents_and_keeps_locked_positions() -> None:
    definition = rich()
    files = dict(encode_directory(definition, node_ids=select_nodes(definition, mutable_only=True)))
    area = "root/roles/Area%3A one%3F"
    files["root/roles/area%3A ONE%3F~2/skills/s1/SKILL.md"] = files.pop(f"{area}/skills/s1/SKILL.md")
    overlay = overlay_directory(definition, files)
    nodes = overlay.candidate.nodes
    assert nodes["Area-2"].members is not None and nodes["Area-2"].members.skills == ("s1",)
    assert nodes["area"].members is not None and nodes["area"].members.skills == ("s2",)
    root = nodes["root"].members
    assert root is not None and root.children == ("area", "locked", "Area-2")
    assert root.pre_process == "pre"


def test_overlay_refuses_what_cannot_form_a_graph() -> None:
    definition = rich()
    files = dict(encode_directory(definition, node_ids=["s1"]))
    with pytest.raises(GraphError):  # same id twice
        overlay_directory(definition, {**files, "root/roles/Area%3A one%3F/skills/dup/SKILL.md":
                                       files["root/roles/Area%3A one%3F/skills/s1/SKILL.md"]})
    with pytest.raises(GraphError):  # folder that is neither a node nor a placeholder
        overlay_directory(definition, {**files, "nowhere/skills/x/SKILL.md": render_front_matter(
            {"description": "x"}, "x")})
    with pytest.raises(GraphError):  # other graph
        manifest = json.loads(files[MANIFEST])
        manifest["graph_id"] = "other"
        overlay_directory(definition, {**files, MANIFEST: json.dumps(manifest).encode()})
    with pytest.raises(GraphError):  # deleting a node another node uses
        overlay_directory(definition, {k: v for k, v in files.items()
                                       if not k.endswith("s1/SKILL.md")})
    with pytest.raises(GraphError):
        overlay_directory(definition, {**files, "../escape/ROLE.md": b""})


def test_text_only_templates_stay_editable_and_edits_are_never_ignored() -> None:
    definition = state(
        node("r", mutable=True, members={"skills": ["a"]}),
        {**{k: v for k, v in skill("a").items() if k != "instructions"},
         "instruction_template": {"parts": [{"kind": "Text", "text": "Review the diff."}]}},
    ).definition
    files = encode_directory(definition)
    raw = files["r/skills/a/SKILL.md"].decode()
    assert raw.endswith("---\nReview the diff.\n") and "\ntemplate: true\n" in raw
    assert not overlay_directory(definition, files).changed
    edited = {**files, "r/skills/a/SKILL.md": raw.replace("the diff.", "it all.").encode()}
    node_a = overlay_directory(definition, edited).candidate.nodes["a"]
    assert node_a.instruction_template is not None
    assert node_a.instruction_template.as_json() == {"parts": [{"kind": "Text", "text": "Review it all."}]}
    both = render_front_matter({"id": "a", "instructions": "json"}, "and a body")
    with pytest.raises(GraphError):
        overlay_directory(definition, {**files, "r/skills/a/SKILL.md": both})


def test_bodies_that_only_json_can_keep_stay_lossless() -> None:
    definition = state(
        node("r", mutable=True, members={"skills": ["a", "b"]}),
        skill("a", instructions="literal {{node:b}} marker\r\nand CR"),
        {**{k: v for k, v in skill("b").items() if k != "instructions"},
         "instruction_template": {"parts": [{"kind": "NodeRef", "target_id": "a", "label": "x}y"}]}},
    ).definition
    files = encode_directory(definition)
    assert b"\ninstructions: " in files["r/skills/a/SKILL.md"]
    assert b"\ninstruction_template: " in files["r/skills/b/SKILL.md"]
    assert not overlay_directory(definition, files).changed


@pytest_asyncio.fixture
async def host() -> AsyncIterator[ManagedGraphHost]:
    with tempfile.TemporaryDirectory(prefix="contexture-directory-") as temp:
        async with ManagedGraphHost.open(Path(temp) / "g", graph_id="g", policy=SingleActorPolicy()) as host:
            yield host


@pytest.mark.asyncio
async def test_host_exports_author_changes_and_imports_as_a_transaction(host: Any) -> None:
    seed = library().definition.as_json()
    host.bootstrap(seed)
    host.change({"op": "update_node", "node_id": "s3", "set_fields": {"instructions": "mine"},
                 "unset_fields": []},
                reason="author edit")
    files = host.export_directory(mutable_only=True, changed_since="1")
    assert json.loads(files[MANIFEST])["nodes"] == ["s3"]
    path = "root/roles/area/roles/sub/skills/s3/SKILL.md"
    fields, _ = parse_front_matter(files[path], path)
    edited = {**files, path: render_front_matter(fields, "mine, revised")}
    overlay, proposal = host.import_directory(edited, reason="import", preview=True)
    assert overlay.updated == ("s3",) and proposal is not None
    assert host.read().definition.nodes["s3"].instructions == "mine"
    overlay, receipt = host.import_directory(edited, reason="import")
    assert receipt["status"] == "committed"
    assert host.read().definition.nodes["s3"].instructions == "mine, revised"
    assert host.import_directory(edited, reason="again") == (overlay_directory(
        host.read().definition, edited), None)
    provenance = host.provenance()
    assert set(provenance) == {"1", "2", "3"}
    assert provenance["3"]["reason"] == "import" and provenance["3"]["actor_ref"]
    assert provenance["2"]["written_at"]


@pytest.mark.asyncio
async def test_import_cannot_touch_locked_nodes(host: Any) -> None:
    seed = library().definition.as_json()
    host.bootstrap(seed)
    files = host.export_directory()  # whole graph, including the locked root
    fields, body = parse_front_matter(files["root/ROLE.md"], "root")
    files = {**files, "root/ROLE.md": render_front_matter(fields, "hijacked")}
    with pytest.raises(GraphError) as error:
        host.import_directory(files, reason="attempt")
    assert error.value.code == "mutation_protected"
    assert host.read().definition.nodes["root"].instructions == "program"
