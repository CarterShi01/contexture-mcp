"""Operation-count and exact-surface oracles; never wall-time assertions."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from contexture.core.graph import policy
from contexture.core.graph.canonical import canonical_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.index import GraphIndex
from contexture.core.graph.model import GraphDefinition, NodeRefPart
from contexture.core.graph.transactions import Limits, _Draft
from scripts.managed_benchmark import Case, generate
from tests.test_managed_transactions import build, node, state


def surface(index: GraphIndex, identity: str) -> bytes:
    """Original full rendered surface, deliberately independent of tokens."""
    graph = index.definition
    spec = graph.nodes[identity]
    refs = list(spec.uses)
    if spec.members:
        refs.extend(n for _, _, n in spec.members.placements())
    if spec.instruction_template:
        refs.extend(p.target_id for p in spec.instruction_template.parts if isinstance(p, NodeRefPart))
    return canonical_json({"node": spec.as_json(), "path": index.path(identity),
        "plane": index.locations[identity].plane,
        "default_locale": graph.presentations.default_locale, "mode": graph.mode,
        "graph_extensions": graph.extensions,
        "cards": [{"node_id": n, "name": graph.nodes[n].name, "path": index.path(n),
            "plane": index.locations[n].plane, "description": graph.nodes[n].description,
            "kind": graph.nodes[n].kind} for n in refs],
        "publications": [p.as_json() for p in graph.publications if p.opens_id == identity],
        "presentations": [{"locale": c.locale, "entry": e.as_json()}
            for c in graph.presentations.catalogs for e in c.entries if e.node_id == identity]})


@pytest.mark.parametrize("shape", ["balanced", "wide", "deep", "cyclic", "high_indegree", "text_schema"])
@pytest.mark.parametrize("edit", ["rename", "description", "numeric", "presentation", "publication", "move"])
def test_surface_tokens_equal_original_canonical_oracle(shape: str, edit: str) -> None:
    raw: Any = generate(Case(nodes=40, shape=shape))
    # Include publications/translations on every object, not just empty pools.
    raw["publications"] = [{"kind": "prompt", "publication_id": "p" + n["node_id"],
        "name": "p" + n["node_id"], "description": "entry", "opens_id": n["node_id"],
        "model_may_open": True} for n in raw["nodes"]]
    raw["presentations"] = {"default_locale": "en", "catalogs": [{"locale": "en", "entries": [
        {"node_id": n["node_id"], "title": n["name"]} for n in raw["nodes"]]}]}
    raw["extensions"] = {"test:number": 1}
    before = GraphDefinition.from_json(raw)
    by_id = {n["node_id"]: n for n in raw["nodes"]}
    if edit == "rename":
        by_id["n000000"]["name"] = "renamed"
    elif edit == "description":
        by_id["n000000"]["description"] = "different card"
    elif edit == "numeric":
        raw["extensions"]["test:number"] = True  # Python == is not JCS equality.
    elif edit == "presentation":
        raw["presentations"]["catalogs"][0]["entries"].reverse()
        raw["presentations"]["catalogs"][0]["entries"][0]["title"] = "new"
    elif edit == "publication":
        raw["publications"].reverse()
        raw["publications"][0]["description"] = "new"
    else:
        # Move last Role to a same-name path's distinct ancestry, preserving
        # relative child order and valid typed membership.
        target = "n000038"
        for n in raw["nodes"]:
            members = n.get("members", {})
            if target in members.get("children", []):
                members["children"].remove(target)
        raw["roots"].append({"node_id": target, "plane": "prompt"})
    after = GraphDefinition.from_json(raw)
    old, new = GraphIndex.build(before), GraphIndex.build(after)
    paths: dict[tuple[int, str], int] = {}
    left, right = policy._surfaces(old, paths), policy._surfaces(new, paths)
    for identity in before.nodes:
        assert (left[identity] == right[identity]) == (surface(old, identity) == surface(new, identity))


@pytest.mark.parametrize("size", [100, 1000])
def test_surface_work_counts_each_node_and_relation_once(size: int) -> None:
    raw: Any = generate(Case(nodes=size, shape="deep"))
    raw["publications"] = [{"kind": "prompt", "publication_id": "p" + str(i), "name": "p" + str(i),
        "description": "entry", "opens_id": n["node_id"], "model_may_open": True}
        for i, n in enumerate(raw["nodes"])]
    raw["presentations"] = {"default_locale": "en", "catalogs": [{"locale": "en", "entries": [
        {"node_id": n["node_id"], "title": "title"} for n in raw["nodes"]]}]}
    graph = GraphDefinition.from_json(raw)
    index = GraphIndex.build(graph)
    paths: dict[tuple[int, str], int] = {}
    with patch.object(policy, "canonical_json", wraps=canonical_json) as encode, patch.object(
            GraphIndex, "path", side_effect=AssertionError("No repeated path materialization")):
        result = policy._surfaces(index, paths)
    assert len(result) == len(paths) == size
    assert encode.call_count == 3 * size + 1  # N nodes + P publications + T translations + metadata.


def test_same_names_under_replaced_ancestor_ids_keep_same_path_token() -> None:
    old = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))
    new = state(node("other", name="r", mutable=True, members={"children": ["a"]}), node("a"),
        roots=[{"node_id": "other", "plane": "model"}])
    paths: dict[tuple[int, str], int] = {}
    a = policy._surfaces(GraphIndex.build(old.definition), paths)
    b = policy._surfaces(GraphIndex.build(new.definition), paths)
    assert a["a"] == b["a"]


def test_draft_metadata_does_not_duplicate_node_pool_or_mutate_base() -> None:
    base = state(node("r", mutable=True, members={"children": ["a"]}), node("a"))
    original = base.definition.digest()
    with patch.object(GraphDefinition, "as_json", side_effect=AssertionError("No redundant graph copy")):
        draft = _Draft(base, None, Limits(), None, None)
    assert "nodes" not in draft.wire and "roots" not in draft.wire
    draft.apply({"op": "rename_node", "node_id": "a", "new_name": "new"})
    assert draft.definition().nodes["a"].name == "new"
    assert base.definition.digest() == original


@pytest.mark.parametrize("limit", [Limits(max_nodes=1), Limits(max_edges=1),
    Limits(max_node_bytes=1), Limits(max_definition_bytes=1)])
def test_final_full_validation_budgets_are_not_bypassed(limit: Limits) -> None:
    base = state(node("r", mutable=True, members={"children": ["a", "b"]}), node("a"), node("b"))
    with pytest.raises(GraphError) as error:
        build(base, {"op": "rename_node", "node_id": "a", "new_name": "new"}, limits=limit)
    assert error.value.code == "limit_exceeded"
    assert base.definition.nodes["a"].name == "a"


def test_empty_reverse_index_keys_and_immutability_preserved() -> None:
    graph = state(node("r", mutable=True, members={"children": ["a"]}), node("a")).definition
    index = GraphIndex.build(graph)
    for mapping in (index.reverse_uses, index.template_refs, index.publications, index.presentation_refs):
        assert set(mapping) == set(graph.nodes)
        assert all(value == () for value in mapping.values())
        with pytest.raises(TypeError):
            target: Any = mapping
            target["a"] = ("tamper",)
