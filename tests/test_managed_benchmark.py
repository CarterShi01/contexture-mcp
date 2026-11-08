"""Small deterministic WP14 equivalence tests; large runs are explicit CLI work."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.managed_benchmark import (
    SHAPES, AllowBenchmark, Case, check_catalog, check_index, compile_definition,
    edit_envelope, generate, oracle, registry_for, sample, summarize,
)
from contexture.core.graph.catalog import metadata_catalog
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import RevisionState
from contexture.core.graph.index import GraphIndex
from contexture.core.graph.model import GraphDefinition
from contexture.core.graph.policy import VerifiedContext
from contexture.core.graph.transactions import prepare


@pytest.mark.parametrize("shape", SHAPES)
def test_deterministic_ordered_index_compiler_catalog(shape: str) -> None:
    case = Case(nodes=48, shape=shape, depth=11, text_bytes=31, schema_fields=3)
    raw = generate(case)
    assert raw == generate(case)
    assert raw != generate(Case(nodes=48, shape=shape, seed=7, depth=11,
                                text_bytes=31, schema_fields=3))
    expected = oracle(raw)
    definition = GraphDefinition.from_json(json.dumps(raw).encode())
    index = GraphIndex.build(definition)
    check_index(index, expected)
    check_index(compile_definition(definition, registry_for(raw)).index, expected)
    assert expected["counts"]["node_visits"] == 48
    assert expected["counts"]["containment_edges"] == 48 - len(raw["roots"])
    assert expected["counts"]["uses_edges"] == (48 if shape == "cyclic"
                                                else 47 if shape == "high_indegree" else 0)
    for visible in (frozenset(index.preorder), frozenset(index.preorder[1::3]), frozenset()):
        material = metadata_catalog(index, visible, kinds=frozenset({"role", "skill", "tool"}),
                                    max_items=200, max_bytes=100000)
        check_catalog(raw, index, expected, material, visible)
    # Raw node-array permutation cannot change semantic traversal or bytes.
    reordered = deepcopy(raw)
    reordered["nodes"].reverse()
    assert GraphDefinition.from_json(reordered).digest() == definition.digest()
    check_index(GraphIndex.build(GraphDefinition.from_json(reordered)), expected)


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("edit", ["rename_root", "update_leaf", "reorder", "add_use", "remove_use"])
def test_single_edit_matches_independent_full_wire_candidate(shape: str, edit: str) -> None:
    raw = generate(Case(nodes=32, shape=shape, depth=7, schema_fields=2))
    expected_raw = deepcopy(raw)
    by_id = {n["node_id"]: n for n in expected_raw["nodes"]}
    first = expected_raw["roots"][0]["node_id"]
    last = next(n["node_id"] for n in raw["nodes"] if n["kind"] == "role"
                and not n["members"]["children"])
    if edit == "rename_root":
        command = dict(op="rename_node", node_id=first, new_name="renamed")
        by_id[first]["name"] = "renamed"
    elif edit == "update_leaf":
        command = dict(op="update_node", node_id=last,
                       set_fields={"description": "edited"}, unset_fields=[])
        by_id[last]["description"] = "edited"
    elif edit == "reorder":
        children = list(reversed(by_id[first]["members"]["children"]))
        command = dict(op="reorder_members", parent_id=first, slot="children", node_ids=children)
        by_id[first]["members"]["children"] = children
    elif edit == "add_use":
        target = next(n for n in by_id if n != first and n not in by_id[first].get("uses", []))
        command = dict(op="add_use", source_id=first, target_id=target, position=0)
        by_id[first]["uses"] = [target, *by_id[first].get("uses", [])]
    else:
        # Install an explicit raw baseline edge, then remove exactly that edge.
        target = next(n for n in by_id if n != first)
        by_id[first]["uses"] = [target]
        next(n for n in raw["nodes"] if n["node_id"] == first)["uses"] = [target]
        command = dict(op="remove_use", source_id=first, target_id=target)
        by_id[first]["uses"] = []
    before = GraphDefinition.from_json(raw)
    before_digest = before.digest()
    prepared = prepare(RevisionState(before, "1"), edit_envelope(before, first, command),
        context=VerifiedContext("benchmark", "benchmark", "1", frozenset({"graph.roots"})),
        admission=AllowBenchmark())
    candidate = prepared.change.candidate
    assert candidate.digest() == GraphDefinition.from_json(expected_raw).digest()
    assert before.digest() == before_digest
    index, expected = GraphIndex.build(candidate), oracle(expected_raw)
    check_index(index, expected)
    material = metadata_catalog(index, frozenset(index.preorder),
        kinds=frozenset({"role", "skill", "tool"}), max_items=200, max_bytes=100000)
    check_catalog(expected_raw, index, expected, material, frozenset(index.preorder))


def test_shape_facts_and_deep_iterative_oracle() -> None:
    deep = oracle(generate(Case(nodes=1500, shape="deep", depth=1400)))
    assert max(v["depth"] for v in deep["locations"].values()) == 1400
    wide = oracle(generate(Case(nodes=100, shape="wide")))
    assert max(v["depth"] for v in wide["locations"].values()) == 1
    hot = oracle(generate(Case(nodes=100, shape="high_indegree")))
    assert len(hot["reverse"]["n000000"]) == 99


def test_catalog_budget_is_real_refusal_not_truncated_success() -> None:
    index = GraphIndex.build(GraphDefinition.from_json(generate(Case(nodes=12))))
    for limits in ({"max_items": 1, "max_bytes": 100000}, {"max_items": 100, "max_bytes": 1}):
        with pytest.raises(GraphError) as error:
            metadata_catalog(index, frozenset(index.preorder),
                             kinds=frozenset({"role", "skill", "tool"}),
                             max_items=limits["max_items"], max_bytes=limits["max_bytes"])
        assert error.value.code == "too_large"


@pytest.mark.parametrize("fields", [{"nodes": 100001}, {"depth": 0}, {"fanout": 0},
    {"shape": "unknown"}, {"text_bytes": 1048577}, {"schema_fields": 1025},
    {"nodes": 100000, "text_bytes": 2000}])
def test_explicit_generator_bounds(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Case(**fields)


def test_small_real_measurement_and_distribution() -> None:
    result = sample(Case(nodes=8, roots=2))
    assert result["oracle_equivalence"] is True
    assert result["input_bytes"] > 0 and result["catalog_bytes"] > 0
    for metric in result["metrics"].values():
        assert metric["wall_seconds"] >= 0 and metric["cpu_seconds"] >= 0
        assert metric["traced_peak_increment_bytes"] >= 0
        assert metric["process_peak_rss_bytes"] > 0
    summary = summarize([result, result])
    assert summary["index_build"]["wall_seconds"]["distribution"] == [
        result["metrics"]["index_build"]["wall_seconds"]] * 2
    with pytest.raises(ValueError):
        summarize([result, {**result, "input_sha256": "different"}])


def test_stage_progress_retains_real_completed_measurements(tmp_path: Path) -> None:
    progress = tmp_path / "partial.json"
    result = sample(Case(nodes=8, roots=2), progress)
    partial = json.loads(progress.read_text())
    assert partial["complete"] is False  # Not an oracle-certified full worker receipt.
    assert partial["completed_metrics"] == result["metrics"]
    assert partial["input_sha256"] == result["input_sha256"]
    assert partial["oracle_counts"] == result["oracle_counts"]


def test_oracle_detects_wrong_order_and_reverse_edges() -> None:
    raw = generate(Case(nodes=24, shape="cyclic"))
    index = GraphIndex.build(GraphDefinition.from_json(raw))
    expected = oracle(raw)
    expected["order"][0:2] = list(reversed(expected["order"][0:2]))
    with pytest.raises(AssertionError):
        check_index(index, expected)
    expected = oracle(raw)
    expected["reverse"][index.preorder[0]] = []
    with pytest.raises(AssertionError):
        check_index(index, expected)