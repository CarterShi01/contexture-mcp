"""Executable WP03 model/index slice, not compiler or managed release claims."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import time
import tracemalloc
from typing import Any, Literal, cast

import jsonschema
import pytest

from contexture.core.graph.canonical import canonical_digest
from contexture.core.graph.errors import GraphError
from contexture.core.graph.index import GraphIndex
from contexture.core.graph.model import (
    BindingDescriptor, GraphDefinition, InstructionTemplate, NodeSpec,
    PublicationContract, normalize_locale,
)


def node(identity: str, kind: str = "role", **fields: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "node_id": identity, "kind": kind, "name": identity, "description": "description",
    }
    if kind != "tool":
        result["instructions"] = ""
    result.update(fields)
    return result


def binding(**fields: Any) -> dict[str, Any]:
    return {"kind": "registered", "binding_id": "reader", "version": "1",
            "portability": {"kind": "portable", "contract_id": "contract-1"}, **fields}


def graph(*nodes: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": "graph",
            "application_name": "app", "mode": "disclosure_only", "nodes": list(nodes or [node("r")]),
            "roots": [{"node_id": (nodes[0]["node_id"] if nodes else "r"), "plane": "model"}],
            "presentations": {"default_locale": "en", "catalogs": []}, **fields}


def prompt(target: str = "r", **fields: Any) -> dict[str, Any]:
    return {"kind": "prompt", "publication_id": "p", "name": "entry", "description": "entry",
            "opens_id": target, "model_may_open": True, **fields}


def load(value: object, **kwargs: Any) -> GraphDefinition:
    return GraphDefinition.from_json(value, **kwargs)


def wire(definition: GraphDefinition) -> dict[str, Any]:
    return cast(dict[str, Any], definition.as_json())


def rejected(value: object, **kwargs: Any) -> GraphError:
    with pytest.raises(GraphError) as error:
        load(value, **kwargs)
    assert error.value.code
    assert len(str(error.value)) <= 1024
    return error.value


def test_normalized_roundtrip_and_schema_oracle() -> None:
    value = graph(node("\U00010000"), node("\ue000", "skill"), roots=[
        {"node_id": "\U00010000", "plane": "model"}, {"node_id": "\ue000", "plane": "prompt"}])
    definition = load(value)
    output = wire(definition)
    assert [n["node_id"] for n in output["nodes"]] == ["\ue000", "\U00010000"]
    assert definition.definition_digest == canonical_digest(output, "contexture.graph/v1")
    assert definition.digest() == load(output).digest() == load(json.dumps(output).encode()).digest()
    assert output["publications"] == [] and output["extensions"] == {}
    assert "members" not in output["nodes"][0]
    assert output["nodes"][1]["members"] == {"children": [], "skills": [], "tools": []}
    schema = json.loads((Path(__file__).parents[1] / "spec/managed/schemas/managed.schema.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(output)


def test_instruction_parts_and_reverse_uses_allow_cycles() -> None:
    r = node("r", uses=["s"], members={"skills": ["s"]})
    del r["instructions"]
    r["instruction_template"] = {"parts": [
        {"kind": "Text", "text": "open "}, {"kind": "NodeRef", "target_id": "s", "label": ""},
        {"kind": "NodeRef", "target_id": "s"}, {"kind": "NodeRef", "target_id": "r"}]}
    definition = load(graph(r, node("s", "skill", uses=["r"])))
    index = GraphIndex.build(definition)
    assert index.reverse_uses == {"r": ("s",), "s": ("r",)}
    assert [(r.source_id, r.part_ordinal) for r in index.template_refs["s"]] == [("r", 1), ("r", 2)]
    assert definition.nodes["r"].instruction_template is not None
    assert wire(definition)["nodes"][0]["instruction_template"] == r["instruction_template"]


def test_typed_slots_preorder_and_root_planes() -> None:
    value = graph(node("r", members={"pre_process": "pre", "children": ["child"],
        "post_process": "post", "skills": ["skill"], "tools": ["tool"]}),
        node("pre", role_variant="pre_process"), node("child", role_variant="post_process"),
        node("post", role_variant="post_process"), node("skill", "skill"), node("tool", "tool"),
        node("root-tool", "tool"), node("root-skill", "skill"),
        roots=[{"node_id": "root-tool", "plane": "prompt"}, {"node_id": "r", "plane": "model"},
               {"node_id": "root-skill", "plane": "model"}])
    index = GraphIndex.build(load(value))
    assert index.preorder == ("root-tool", "r", "pre", "child", "post", "skill", "tool", "root-skill")
    assert [n.node_id for n in index.subtree("r")] == ["r", "pre", "child", "post", "skill", "tool"]
    assert [n.node_id for n in index.subtree("r", include_self=False)] == ["pre", "child", "post", "skill", "tool"]
    assert index.locations["post"].slot == "post_process"
    assert index.locations["post"].member_ordinal == 2
    assert index.locations["post"].ordinal == 0
    assert index.locations["post"].root_ordinal == 1
    assert [n.node_id for n in index.walk(plane="prompt")] == ["root-tool"]
    assert index.get("/r/skill", by="path") is index.get("skill")
    assert index.path("post") == "/r/post"


@pytest.mark.parametrize("policy,expected", [
    (["inherit", True, "inherit"], [False, True, True]),
    ([True, "inherit", False, True], [True, True, False, False]),
    ([False, True, True], [False, False, False]),
    (["inherit", "inherit"], [False, False]),
])
def test_effective_mutability_false_barrier(policy: list[Any], expected: list[bool]) -> None:
    nodes = [node(str(i), mutable=p, members={"children": [str(i + 1)] if i + 1 < len(policy) else []})
             for i, p in enumerate(policy)]
    index = GraphIndex.build(load(graph(*nodes)))
    assert [index.locations[str(i)].effective_mutable for i in range(len(policy))] == expected


@pytest.mark.parametrize("field,value", [
    ("schema", "unknown"), ("semantic_version", 1), ("mode", "unknown"),
    ("application_name", "a/b"), ("graph_id", "\ud800"), ("graph_id", "é" * 129),
    ("publications", None), ("roots", []), ("nodes", []), ("presentations", None),
    ("channels", {}), ("revision", "1"), ("extensions", {"unqualified": 1}),
])
def test_invalid_graph_fields(field: str, value: Any) -> None:
    rejected(graph(**{field: value}))


@pytest.mark.parametrize("fields", [
    {"kind": "future"}, {"name": ""}, {"name": "a\x7fb"}, {"name": "a\x85b"},
    {"description": ""}, {"mutable": "true"}, {"mutable": 1}, {"mutable": None},
    {"uses": ["r"]}, {"uses": ["missing"]}, {"uses": ["s", "s"]},
    {"members": {"unknown": []}}, {"members": {"pre_process": []}},
    {"members": {"post_process": None}}, {"members": {"children": ["missing"]}},
    {"members": {"children": ["r"]}}, {"binding": binding()}, {"input_contract": {}},
    {"instruction_template": {"parts": []}}, {"parent": "x"},
    {"extensions": {"bad:key\n": 1}}, {"extensions": {"a:x": 2**53}},
    {"instructions": None}, {"instructions": "\ud800"},
])
def test_invalid_node_fields(fields: dict[str, Any]) -> None:
    rejected(graph(node("r", **fields)))


@pytest.mark.parametrize("member,slot", [("role", "tools"), ("tool", "skills"),
    ("skill", "children"), ("role", "pre_process"), ("role", "post_process")])
def test_invalid_typed_slots(member: str, slot: str) -> None:
    payload: Any = "s" if slot in ("pre_process", "post_process") else ["s"]
    rejected(graph(node("r", members={slot: payload}), node("s", member)))


def test_forest_alias_cycles_orphans_and_names() -> None:
    rejected(graph(node("r"), node("r")))
    rejected(graph(node("r"), node("orphan")))
    rejected(graph(node("r", members={"children": ["s", "s"]}), node("s")))
    rejected(graph(node("r", members={"children": ["s", "t"]}),
                   node("s", members={"children": ["u"]}),
                   node("t", members={"children": ["u"]}), node("u")))
    rejected(graph(node("r"), node("a", members={"children": ["b"]}),
                   node("b", members={"children": ["a"]})))
    rejected(graph(node("r", members={"children": ["s"]}), node("s"),
                   roots=[{"node_id": "r", "plane": "model"}, {"node_id": "s", "plane": "prompt"}]))
    rejected(graph(node("r", members={"children": ["s"], "skills": ["t"]}),
                   node("s", name="same"), node("t", "skill", name="same")))
    rejected(graph(node("r"), node("t", "tool", name="r"), roots=[
        {"node_id": "r", "plane": "model"}, {"node_id": "t", "plane": "prompt"}]))
    rejected(graph(roots=[{"node_id": "r", "plane": "prompt"}]))


@pytest.mark.parametrize("kind,fields", [
    ("skill", {"members": {}}), ("skill", {"role_variant": "ordinary"}),
    ("tool", {"instructions": "x"}), ("tool", {"members": {}}),
    ("tool", {"binding": binding()}), ("tool", {"input_contract": False}),
])
def test_kind_and_disclosure_matrix(kind: str, fields: dict[str, Any]) -> None:
    rejected(graph(node("r", kind, **fields)))


@pytest.mark.parametrize("part", [
    {"kind": "NodeRef", "target_id": "missing"}, {"kind": "NodeRef", "target_id": "r", "label": None},
    {"kind": "Text", "text": 1}, {"kind": "Text", "text": "", "extra": 1}, {"kind": "Ref"},
])
def test_template_validation(part: object) -> None:
    raw = node("r")
    del raw["instructions"]
    raw["instruction_template"] = {"parts": [part]}
    rejected(graph(raw))


@pytest.mark.parametrize("fields", [
    {"kind": "dynamic"}, {"config": []}, {"secret_refs": ["a", "a"]},
    {"expected_contract_digest": "sha256:" + "a" * 64 + "\n"},
    {"expected_contract_digest": None}, {"handler": "code"},
    {"portability": {"kind": "native", "language": "ruby", "version": "1", "factory": "f"}},
    {"portability": {"kind": "portable", "contract_id": "c", "factory": "f"}},
])
def test_binding_validation(fields: dict[str, Any]) -> None:
    rejected(graph(node("t", "tool", binding=binding(**fields)), mode="runtime"))


def test_runtime_binding_contract_and_native_portability() -> None:
    rejected(graph(node("t", "tool"), mode="runtime"))
    raw = graph(node("t", "tool", binding=binding(portability={"kind": "native", "language": "python",
        "version": "3.11", "factory": "package:factory"}), input_contract=False), mode="runtime")
    definition = load(raw)
    assert definition.nodes["t"].input_contract is False
    assert wire(definition)["nodes"][0]["binding"]["config"] == {}
    assert definition.digest() == load(definition.as_json()).digest()


def test_contract_assertion_schema_and_execution_data_immutability() -> None:
    raw_binding = binding(config={"nested": [{"value": None}]}, secret_refs=["vault:handle"])
    raw = graph(node("t", "tool", binding=raw_binding,
        input_contract={"type": "object", "properties": {"x": {"type": "string"}}}), mode="runtime")
    definition = load(raw)
    digest = definition.digest()
    b = definition.nodes["t"].binding
    assert b is not None
    with pytest.raises(TypeError):
        cast(Any, b.config)["nested"][0]["value"] = 1
    with pytest.raises(TypeError):
        cast(Any, definition.nodes["t"].input_contract)["type"] = "number"
    raw_binding["config"]["nested"][0]["value"] = "external"
    assert definition.digest() == digest
    for bad in [{"type": "not-a-type"}, {"required": "x"}, {"properties": {"x": 1}}]:
        rejected(graph(node("t", "tool", binding=binding(), input_contract=bad), mode="runtime"))
    # Syntax inspection must not fetch a remote author-supplied reference.
    load(graph(node("t", "tool", binding=binding(),
        input_contract={"$ref": "https://invalid.invalid/no-fetch"}), mode="runtime"))


def test_index_reverse_order_independent_of_node_pool_order() -> None:
    raw = graph(node("r", members={"children": ["b", "a"]}),
        node("a", uses=["r"]), node("b", uses=["r"]))
    first = load(raw)
    raw["nodes"].reverse()
    second = load(raw)
    assert first.digest() == second.digest()
    assert GraphIndex.build(first).reverse_uses == GraphIndex.build(second).reverse_uses
    assert GraphIndex.build(first).reverse_uses["r"] == ("b", "a")


def test_immutable_definition_digest_is_computed_once_and_detached(monkeypatch: pytest.MonkeyPatch) -> None:
    from contexture.core.graph import model
    calls = 0
    original = model.canonical_digest

    def counted(value: object, domain: str) -> str:
        nonlocal calls
        calls += 1
        return original(value, domain)

    monkeypatch.setattr(model, "canonical_digest", counted)
    raw = graph(node("r", instructions="Exact literal"))
    definition = load(raw)
    expected = canonical_digest(definition.as_json(), "contexture.graph/v1")
    for _ in range(100):
        assert definition.digest() == expected
        assert definition.definition_digest == expected
    assert calls == 1
    detached = wire(definition)
    detached["nodes"][0]["instructions"] = "Different"
    assert definition.digest() == expected
    changed = load(detached)
    assert changed.digest() != expected
    assert calls == 2
    with pytest.raises(FrozenInstanceError):
        definition._cached_digest = "sha256:" + "0" * 64
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert set(executor.map(lambda _: definition.digest(), range(100))) == {expected}


def test_semantic_order_and_literal_text_are_digest_relevant() -> None:
    raw = graph(node("r", members={"children": ["a", "b"]}), node("a"), node("b"))
    first = load(raw)
    raw["nodes"][0]["members"]["children"].reverse()
    assert first.digest() != load(raw).digest()
    plain = load(graph(node("r", instructions="é")))
    decomposed = load(graph(node("r", instructions="e\u0301")))
    assert plain.digest() != decomposed.digest()
    assert load(graph(node("r"))).digest() == load(graph(node("r", mutable="inherit", uses=[],
        extensions={}, role_variant="ordinary", members={"children": [], "skills": [], "tools": []}),
        extensions={}, publications=[])).digest()


def test_publication_registry_evidence_and_effects() -> None:
    resource = {"kind": "resource", "publication_id": "p", "name": "resource", "uri": "app://data",
                "mime_type": "application/json", "opens_id": "t"}
    b = BindingDescriptor.from_json(binding())
    raw = graph(node("t", "tool", binding=b.as_json()), mode="runtime", publications=[resource])
    assert rejected(raw).code == "binding_contract_required"
    for effect, free in [("write", True), ("read_only", False)]:
        rejected(raw, publication_contracts={"t": PublicationContract(
            b, cast(Literal["read_only", "write"], effect), free)})
    contracts = {"t": PublicationContract(b, "read_only", True)}
    definition = load(raw, publication_contracts=contracts)
    index = GraphIndex.build(definition, publication_contracts=contracts)
    assert index.publications["t"] == ("p",)
    # Evidence is deliberately not author-controlled or definition-hashed.
    assert "effect" not in wire(definition)["nodes"][0]
    for method in ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]:
        route = {"kind": "route", "publication_id": "p", "method": method, "path": "/entry", "opens_id": "t"}
        candidate = {**raw, "publications": [route]}
        expected = "read_only" if method in ["GET", "HEAD"] else "write"
        load(candidate, publication_contracts={"t": PublicationContract(b, expected, False)})
        rejected(candidate, publication_contracts={"t": PublicationContract(
            b, "write" if expected == "read_only" else "read_only", False)})
    stale = BindingDescriptor.from_json(binding(version="2"))
    rejected(raw, publication_contracts={"t": PublicationContract(stale, "read_only", True)})


def test_publication_duplicates_modes_and_plane_reservations() -> None:
    raw = graph(node("r"), node("s", "skill"), roots=[{"node_id": "r", "plane": "model"},
        {"node_id": "s", "plane": "prompt"}], publications=[prompt("s")])
    index = GraphIndex.build(load(raw))
    assert not index.model_may_open("s") and index.model_may_open("r")
    raw["publications"].append(prompt("r", publication_id="other", name="other", model_may_open=False))
    assert not GraphIndex.build(load(raw)).model_may_open("r")
    rejected(graph(publications=[prompt(), prompt(name="different")]))
    rejected(graph(publications=[prompt(), prompt(publication_id="other")]))
    rejected(graph(publications=[prompt("missing")]))
    rejected(graph(publications=[prompt(model_may_open=1)]))
    rejected(graph(publications=[prompt(extra=True)]))
    rejected(graph(publications=[{"kind": "route", "publication_id": "x", "method": "GET",
                                "path": "/", "opens_id": "r"}]))
    b = BindingDescriptor.from_json(binding())
    contracts = {"t": PublicationContract(b, "read_only", True)}
    p = {"kind": "resource", "publication_id": "p", "name": "resource", "uri": "app://data",
         "mime_type": "application/json", "opens_id": "t"}
    base = graph(node("t", "tool", binding=b.as_json()), mode="runtime")
    rejected({**base, "publications": [p, {**p, "publication_id": "q", "name": "other"}]},
             publication_contracts=contracts)
    route = {"kind": "route", "publication_id": "p", "method": "GET", "path": "/x", "opens_id": "t"}
    rejected({**base, "publications": [route, {**route, "publication_id": "q"}]},
             publication_contracts=contracts)


@pytest.mark.parametrize("locale,expected", [
    ("EN-latn-us", "en-Latn-US"), ("zh-cmn-hans-CN", "zh-cmn-Hans-CN"),
    ("de-DE-u-co-phonebk", "de-DE-u-co-phonebk"), ("x-PRIVATE", "x-private"),
    ("I-klingon", "i-klingon"), ("sgn-be-fr", "sgn-BE-FR"), ("en-a-foo-x-A", "en-a-foo-x-a"),
])
def test_locale_syntax_and_casing(locale: str, expected: str) -> None:
    assert normalize_locale(locale) == expected


@pytest.mark.parametrize("locale", ["en_US", "en\n", "e", "en-x", "en-u", "en-u-a",
    "en-variant-variant", "en-u-foo-u-bar", "en-123456789", "éé", "x", "en--US"])
def test_invalid_locale_including_inactive(locale: str) -> None:
    rejected(graph(presentations={"default_locale": "en", "catalogs": [{"locale": locale, "entries": []}]}))


def test_presentations_all_references_and_fallback() -> None:
    raw = graph(presentations={"default_locale": "fr", "catalogs": [
        {"locale": "en", "entries": [{"node_id": "r", "title": "English"}]},
        {"locale": "fr", "entries": [{"node_id": "r", "description": "French"}]},
        {"locale": "zh-Hant", "entries": [{"node_id": "r", "title": "Traditional"}]}]})
    index = GraphIndex.build(load(raw))
    english = index.presentation("r", "en-US")
    traditional = index.presentation("r", "zh-Hant-TW-u-co-stroke")
    french = index.presentation("r", "de")
    assert english is not None and english.title == "English"
    assert traditional is not None and traditional.title == "Traditional"
    assert french is not None and french.description == "French"
    assert index.presentation_refs["r"] == ("en", "fr", "zh-Hant")
    for catalogs in [
        [{"locale": "en", "entries": [{"node_id": "missing", "title": "x"}]}],
        [{"locale": "en", "entries": []}, {"locale": "EN", "entries": []}],
        [{"locale": "en", "entries": [{"node_id": "r", "title": "a"}, {"node_id": "r", "title": "b"}]}],
        [{"locale": "en", "entries": [{"node_id": "r", "instructions": "x"}]}],
    ]:
        rejected(graph(presentations={"default_locale": "en", "catalogs": catalogs}))


def test_deep_immutability_detachment_aliases_and_old_snapshot_concurrency() -> None:
    shared: dict[str, Any] = {"nested": [{"value": None}]}
    raw = graph(node("r", extensions={"a:x": shared, "a:y": shared}), extensions={"a:x": shared})
    original = deepcopy(raw)
    definition = load(raw)
    index = GraphIndex.build(definition)
    digest = definition.digest()
    shared["nested"][0]["value"] = "changed"
    raw["nodes"][0]["name"] = "renamed"
    exported = wire(definition)
    exported["nodes"][0]["extensions"]["a:x"]["nested"][0]["value"] = "external"
    with pytest.raises(FrozenInstanceError):
        setattr(definition.nodes["r"], "name", "x")
    with pytest.raises(TypeError):
        cast(Any, definition.nodes["r"].extensions)["a:x"]["nested"][0]["value"] = "x"
    with pytest.raises(TypeError):
        cast(Any, index.locations)["r"] = index.locations["r"]
    with pytest.raises(TypeError):
        cast(Any, index.child_names[None])["x"] = "r"
    with pytest.raises(TypeError):
        GraphDefinition()
    with pytest.raises(TypeError):
        NodeSpec()
    assert definition.digest() == digest == load(original).digest()
    def reader(_: int) -> tuple[str, str]:
        return index.path("r"), definition.digest()
    with ThreadPoolExecutor(max_workers=8) as pool:
        future = pool.map(reader, range(100))
        replacement = load(raw)
        assert replacement.nodes["r"].name == "renamed"
        assert all(result == ("/r", digest) for result in future)
    assert index.get("/r", by="path").name == "r"


@pytest.mark.parametrize("raw", [
    b'{"schema":1,"schema":2}', b'\xef\xbb\xbf{}', b'{"x":NaN}',
    b'{"x":9007199254740993}', b'{"x":1e-9999}', b'{"x":"\\ud800"}',
])
def test_strict_raw_decode(raw: bytes) -> None:
    rejected(raw)


def test_non_json_and_cyclic_python_inputs() -> None:
    cycle: dict[str, Any] = {}
    cycle["a:x"] = cycle
    rejected(graph(extensions=cycle))
    rejected(graph(extensions={"a:x": object()}))
    rejected(graph(extensions={"a:x": float("inf")}))
    assert load(graph(extensions={"a:x": None})).digest() != load(graph()).digest()


def test_id_vs_path_ambiguity_and_missing_lookup() -> None:
    index = GraphIndex.build(load(graph(node("/opaque", name="r"))))
    assert index.get("/opaque").node_id == "/opaque"
    assert index.get("/r", by="path").node_id == "/opaque"
    for identity, by in [("r", "path"), ("/r/", "path"), ("missing", "id"), ("r", "ref")]:
        with pytest.raises(GraphError):
            index.get(identity, by=by)


@pytest.mark.parametrize("depth", [3000, 5000])
def test_deep_forest_iterative_linear_memory(depth: int, record_property: Any) -> None:
    nodes = [node(str(i), name="n", mutable=True if i == 0 else "inherit",
        members={"children": [str(i + 1)] if i < depth - 1 else []}) for i in range(depth)]
    raw = graph(*nodes)
    start = time.perf_counter()
    tracemalloc.start()
    try:
        definition = load(raw)
        index = GraphIndex.build(definition)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    elapsed = time.perf_counter() - start
    record_property("depth", depth)
    record_property("peak_bytes", peak)
    record_property("seconds", elapsed)
    print(f"DEEP_MODEL depth={depth} peak_bytes={peak} seconds={elapsed:.3f}")
    assert len(index.preorder) == depth
    assert index.locations[str(depth - 1)].depth == depth - 1
    assert index.locations[str(depth - 1)].effective_mutable
    assert index.locations["0"].subtree_end == depth
    path = "/" + "/".join(["n"] * depth)
    assert index.path(str(depth - 1)) == path
    assert index.get(path, by="path").node_id == str(depth - 1)
    assert sum(1 for _ in index.subtree("0")) == depth
    assert definition.digest() == load(definition.as_json()).digest()
    # Generous RSS-independent allocation bound catches ancestor tuples/full
    # paths cached per node. Actual wall time is evidence, not a portable SLA.
    assert peak < depth * 16000