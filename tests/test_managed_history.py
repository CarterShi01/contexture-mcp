"""WP05 immutable history, exact diff and proposal-only revert tests."""

from __future__ import annotations

from typing import Any

import pytest

from contexture.core.graph.diff import behavior_diff, definition_diff, execution_diff, surface_diff
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import RevisionState, change_event, resolve_facts
from contexture.core.graph.history import History, revert_candidate
from contexture.core.graph.model import GraphDefinition

from tests.test_managed_events import graph, node, oracle


def append(history: History, candidate: GraphDefinition, sequence: str) -> History:
    change = resolve_facts(history.head, candidate)
    event = change_event(history.head, change, command_digest="sha256:" + "1" * 64, reason="test")
    return history.append(event, journal_seq=sequence, tombstones=change.tombstones)

def test_append_validates_only_new_link_but_public_construction_checks_all_links(monkeypatch: pytest.MonkeyPatch) -> None:
    from contexture.core.graph import history as module
    calls = 0
    original = module.parse_counter

    def counted(value: object) -> int:
        nonlocal calls
        calls += 1
        return original(value)

    history = History.seed(RevisionState(graph(), "1"), journal_seq="1")
    monkeypatch.setattr(module, "parse_counter", counted)
    for sequence in range(2, 102):
        history = append(history, graph(node("r", name=f"node-{sequence}")), str(sequence))
    assert calls == 300
    before = calls
    rebuilt = History(history.records)
    assert calls - before == 200
    assert rebuilt == history
    invalid = (history.records[-1], *history.records[:-1])
    with pytest.raises(GraphError):
        History(invalid)


def test_history_is_immutable_retained_and_ordered_not_wall_time() -> None:
    seed = History.seed(RevisionState(graph(), "8"), journal_seq="20")
    renamed = append(seed, graph(node("r", name="renamed")), "30")
    restored = append(renamed, graph(), "31")
    noop = append(restored, graph(), "35")
    assert len(seed.records) == 1 and len(noop.records) == 4
    assert noop.retained_lower_bound == "8" and noop.head.revision == "10"
    assert noop.at("9").definition.nodes["r"].name == "renamed"
    assert noop.diff("8", "10").empty  # endpoint net state, not event process
    assert len(noop.for_node("r")) == 2
    with pytest.raises(GraphError) as error:
        noop.at("7")
    assert error.value.code == "history_unavailable"
    with pytest.raises(GraphError):
        append(noop, graph(), "35")
    with pytest.raises(TypeError):
        noop.records[1].event["reason"] = "tampered"  # type: ignore[index]


def test_append_shares_immutable_list_without_materializing_tuple_prefix() -> None:
    seed = History.seed(RevisionState(graph(), "1"), journal_seq="1")
    change = resolve_facts(seed.head, seed.head.definition)
    event = change_event(seed.head, change, command_digest="sha256:" + "1" * 64, reason="test")
    history = seed
    for sequence in range(2, 10002):
        history = history.append(event, journal_seq=str(sequence), tombstones=[])
        assert history._cached_records is None
    assert history.head is seed.head and history.at("1") is seed.head
    assert history._cached_records is None
    assert len(seed.records) == 1
    assert isinstance(history.records, tuple) and len(history.records) == 10001
    assert history.records is history.records
    assert History(history.records) == history
    left = append(seed, graph(node("r", name="left")), "2")
    right = append(seed, graph(node("r", name="right")), "2")
    assert seed.head.definition.nodes["r"].name == "r"
    assert left.head.definition.nodes["r"].name == "left"
    assert right.head.definition.nodes["r"].name == "right"


def test_diff_shape_stable_id_move_and_exact_optional_values() -> None:
    base = graph(node("r", members={"children": ["a", "b"]}), node("a"), node("b"))
    candidate = graph(node("r", members={"children": ["b", "a"]}), node("a", name="new-name"), node("b"))
    difference = definition_diff(base, candidate)
    oracle(difference.as_json(), "DefinitionDiff")
    output: Any = difference.as_json()
    assert len(output["membership"]) == 1
    assert output["membership"][0]["field"] == "/members"
    assert output["nodes"][0]["id"] == "a" and output["nodes"][0]["field"] == "/name"
    output["nodes"].clear()
    assert not difference.empty
    entries: Any = difference.as_json()["nodes"]
    assert all(e["field"] for e in entries)  # no false delete/create


def test_diff_captures_unencodable_presentation_default_and_order() -> None:
    base = graph()
    candidate = graph(presentations={"default_locale": "fr", "catalogs": [
        {"locale": "fr", "entries": [{"node_id": "r", "title": "titre"}]}]})
    diff = definition_diff(base, candidate)
    oracle(diff.as_json(), "DefinitionDiff")
    assert diff.as_json()["locales"]


def test_revert_returns_new_proposal_preserves_unrelated_changes() -> None:
    old = RevisionState(graph(node("r", members={"children": ["a", "b"]}), node("a"), node("b")), "1")
    current = RevisionState(graph(node("r", members={"children": ["a", "b"]}),
        node("a", description="mine"), node("b", description="concurrent")), "3")
    proposal = revert_candidate(current, old, node_ids=("a",))
    assert proposal.base_revision == "3" and proposal.target_revision == "1"
    assert proposal.change.candidate.nodes["a"].description == "description"
    assert proposal.change.candidate.nodes["b"].description == "concurrent"
    assert current.definition.nodes["a"].description == "mine" and current.revision == "3"
    assert not hasattr(proposal, "commit") and not hasattr(proposal, "write")
    with pytest.raises(GraphError):
        revert_candidate(current, old, node_ids=("a", "a"))


def test_revert_restores_identity_without_erasing_tombstone_history() -> None:
    old = RevisionState(graph(node("r", members={"children": ["a"]}), node("a", mutable=False)), "1")
    history = append(History.seed(old, journal_seq="1"), graph(), "2")
    proposal = revert_candidate(history.head, old)
    assert proposal.change.tombstones == history.head.tombstones
    restored = append(history, proposal.change.candidate, "3")
    assert restored.head.revision == "3" and restored.head.tombstones == history.head.tombstones
    assert history.head.definition.nodes.keys() == {"r"}


def test_projection_comparisons_require_actual_distinct_dependencies() -> None:
    # Equal definition never proves equal manifests or rendered surfaces.
    assert definition_diff(graph(), graph()).empty
    assert execution_diff({"registry": "a"}, {"registry": "b"})["equal"] is False
    assert surface_diff({"locale": "en"}, {"locale": "fr"})["equal"] is False
    assert behavior_diff({"evaluation": [True]}, {"evaluation": [False]})["equal"] is False
    for compare in (behavior_diff, surface_diff, execution_diff):
        with pytest.raises(GraphError):
            compare(graph(), graph())


def test_history_node_filter_excludes_unrelated_publications() -> None:
    definition = graph(node("r", members={"children": ["a"]}), node("a"))
    history = History.seed(RevisionState(definition, "1"), journal_seq="1")
    wire: Any = definition.as_json()
    wire["publications"] = [{"kind": "prompt", "publication_id": "p", "name": "p",
        "description": "p", "opens_id": "r", "model_may_open": True}]
    changed = append(history, GraphDefinition.from_json(wire), "5")
    assert len(changed.for_node("r")) == 1 and changed.for_node("a") == ()
    assert len(changed.for_publication("p")) == 1
    assert changed.for_publication("other") == ()


def test_revert_refuses_unretained_identity_and_incomplete_scope_closure() -> None:
    past = RevisionState(graph(node("r", members={"children": ["a"]}), node("a")), "1")
    no_evidence = RevisionState(graph(), "2")
    with pytest.raises(GraphError, match="tombstones"):
        revert_candidate(no_evidence, past)
    with pytest.raises(GraphError):
        revert_candidate(no_evidence, past, node_ids=("a",))
    with pytest.raises(GraphError):
        revert_candidate(past, no_evidence)


def test_history_bootstrap_migration_and_ordered_translation_revert() -> None:
    from contexture.core.graph.canonical import thaw_json
    from contexture.core.graph.events import UnseededState, bootstrap_event, resolve_migration
    unseeded = UnseededState("g")
    event = bootstrap_event(unseeded, graph(node("r", mutable=False)),
        command_digest="sha256:" + "1" * 64, reason="seed", approval_ref="bootstrap-approved")
    initial = History.bootstrap(unseeded, event, journal_seq="4")
    assert initial.retained_lower_bound == "1" and initial.records[0].event is not None
    candidate = graph(node("r", mutable=True), presentations={"default_locale": "fr",
        "catalogs": [{"locale": "fr", "entries": []}, {"locale": "en", "entries": [
            {"node_id": "r", "title": "R"}]}]})
    exact = definition_diff(initial.head.definition, candidate).as_json()
    change = resolve_migration(initial.head, candidate, migration_id="m", exact_diff=exact,
        source_mapping={"retained_ids": ["r"], "new_ids": [], "deleted_ids": []})
    migrated = change_event(initial.head, change, command_digest="sha256:" + "1" * 64, reason="upgrade",
        migration={"kind": "migration", "migration_id": "m", "approval_ref": "approved",
            "source_digest": initial.head.definition_digest, "exact_diff": exact})
    history = initial.append(thaw_json(migrated), journal_seq="9", tombstones=change.tombstones)
    assert history.at("1").definition.nodes["r"].mutable is False
    assert history.at("2").definition.nodes["r"].mutable is True
    # Revert is still ordinary: historical creation policy must not be reset.
    with pytest.raises(GraphError):
        revert_candidate(history.head, initial.head)
    reordered = graph(node("r", mutable=True), presentations={"default_locale": "fr",
        "catalogs": [{"locale": "en", "entries": [{"node_id": "r", "title": "R"}]},
                     {"locale": "fr", "entries": []}]})
    final = append(history, reordered, "11")
    assert final.for_node("r")[-1].state.revision == "3"
    proposal = revert_candidate(final.head, history.head)
    assert proposal.change.candidate.digest() == candidate.digest()
    assert proposal.change.facts[0]["type"] == "PresentationsAssigned"