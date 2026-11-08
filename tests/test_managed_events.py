"""WP05 pure fact/replay evidence against the independent candidate schema."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from contexture.core.graph.canonical import canonical_digest, canonical_json, thaw_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.events import (
    BootstrapCandidate, RevisionState, UnseededState, apply_event, apply_facts, bootstrap_event,
    change_event, facts_digest, resolve_facts, resolve_migration,
)
from contexture.core.graph.model import GraphDefinition


def node(identity: str, **fields: Any) -> dict[str, Any]:
    return {"node_id": identity, "kind": "role", "name": identity,
            "description": "description", "instructions": "", "mutable": True, **fields}


def graph(*nodes: dict[str, Any], **fields: Any) -> GraphDefinition:
    return GraphDefinition.from_json({"schema": "contexture.graph/v1", "semantic_version": "1",
        "graph_id": "g", "application_name": "app", "mode": "disclosure_only",
        "nodes": list(nodes or [node("r")]), "roots": [{"node_id": "r", "plane": "model"}],
        "presentations": {"default_locale": "en", "catalogs": []}, **fields})


def oracle(value: object, definition: str) -> None:
    schema = json.loads((Path(__file__).parents[1] / "spec/managed/schemas/managed.schema.json").read_text())
    jsonschema.Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/" + definition}).validate(
        thaw_json(value))


def replay(base: RevisionState, candidate: GraphDefinition) -> RevisionState:
    change = resolve_facts(base, candidate)
    for fact in change.facts:
        oracle(fact, "Fact")
    event = change_event(base, change, command_digest=canonical_digest({}, "contexture.command/v1"), reason="test")
    oracle(event, "Event")
    result = apply_event(base, event, tombstones=change.tombstones)
    assert result.definition_digest == candidate.digest()
    return result


def test_complete_atomic_multiple_changes_and_uses_cycles() -> None:
    base = RevisionState(graph(node("r", members={"children": ["a", "b"]}), node("a"), node("b")), "4")
    renamed = node("a", name="b", uses=["r"], instruction_template={
        "parts": [{"kind": "NodeRef", "target_id": "new"}]})
    renamed.pop("instructions")
    candidate = graph(node("r", members={"children": ["b", "a", "new"]}, uses=["a"]),
        renamed, node("b", name="a"), node("new"))
    # Name swap and literal->template are illegal as sequential graph states,
    # but must be accepted as one complete candidate.
    result = replay(base, candidate)
    assert result.revision == "5"
    assert base.definition.nodes["a"].name == "a"


def test_explicit_absent_and_json_null_inside_extensions() -> None:
    base = RevisionState(graph(), "1")
    wire: Any = base.definition.as_json()
    wire["nodes"][0].pop("instructions")
    wire["nodes"][0]["instruction_template"] = {"parts": []}
    wire["nodes"][0]["extensions"] = {"test:null": None}
    candidate = GraphDefinition.from_json(wire)
    facts: Any = thaw_json(resolve_facts(base, candidate).facts)
    assert facts[0]["after"]["instructions"] == {"present": False}
    assert facts[0]["before"]["instruction_template"] == {"present": False}
    assert facts[0]["after"]["extensions"]["value"]["test:null"] is None
    replay(base, candidate)


@pytest.mark.parametrize("mutation", ["before", "missing", "reverse", "duplicate", "unknown", "extra",
                                     "wrong-id", "after", "empty-field", "null-absent"])
def test_corrupt_facts_fail_closed(mutation: str) -> None:
    base = RevisionState(graph(node("r", members={"children": ["a"]}), node("a")), "1")
    candidate = graph(node("r", description="changed", members={"children": ["a"]}, uses=["a"]), node("a"))
    change = resolve_facts(base, candidate)
    facts: Any = thaw_json(change.facts)
    if mutation == "before":
        facts[0]["before"]["description"]["value"] = "forged"
    elif mutation == "missing":
        facts.pop()
    elif mutation == "reverse":
        facts.reverse()
    elif mutation == "duplicate":
        facts.append(deepcopy(facts[0]))
    elif mutation == "unknown":
        facts[0]["type"] = "ToolExecuted"
    elif mutation == "extra":
        facts[0]["ignored"] = True
    elif mutation == "wrong-id":
        facts[0]["node_id"] = "absent"
    elif mutation == "after":
        facts[0]["after"]["description"]["value"] = "tampered"
    elif mutation == "empty-field":
        facts[0]["after"] = {}
    else:
        facts[0]["after"]["description"] = {"present": False, "value": None}
    with pytest.raises(GraphError):
        apply_facts(base, facts, tombstones=change.tombstones, revision="2",
                    before_digest=base.definition_digest, after_digest=candidate.digest())
    assert base.revision == "1" and base.definition.nodes["r"].description == "description"


@pytest.mark.parametrize("field,value", [("base_revision", "0"), ("result_revision", "01"),
    ("result_revision", 2), ("result_revision", "3"), ("graph_id", "wrong"),
    ("semantic_version", "2"), ("before_digest", "sha256:" + "0" * 64),
    ("after_digest", "sha256:" + "0" * 64), ("command_digest", "x"), ("reason", "")])
def test_event_rejects_stale_counters_digests_and_cross_graph(field: str, value: Any) -> None:
    base = RevisionState(graph(), "1")
    change = resolve_facts(base, graph(node("r", name="renamed")))
    event: Any = thaw_json(change_event(base, change, command_digest="sha256:" + "1" * 64, reason="test"))
    event[field] = value
    with pytest.raises(GraphError):
        apply_event(base, event, tombstones=change.tombstones)


def test_delete_restore_retains_every_tombstone_and_identity_policy() -> None:
    original = graph(node("r", members={"children": ["a", "b"]}), node("a", mutable=False), node("b"))
    base = RevisionState(original, "1")
    deleted = replay(base, graph())
    assert [t["node_id"] for t in deleted.tombstones] == ["a", "b"]
    assert all(t["deleted_revision"] == "2" for t in deleted.tombstones)
    restored = replay(deleted, original)
    assert restored.tombstones == deleted.tombstones
    assert [f["type"] for f in resolve_facts(deleted, original).facts][:2] == ["NodeRestored", "NodeRestored"]
    forged = graph(node("r", members={"children": ["a", "b"]}), node("a", mutable=True), node("b"))
    with pytest.raises(GraphError):
        resolve_facts(deleted, forged)
    change = resolve_facts(deleted, original)
    with pytest.raises(GraphError):
        apply_facts(deleted, change.facts, tombstones=[], revision="3",
                    before_digest=deleted.definition_digest, after_digest=original.digest())
    again = replay(restored, graph())
    assert len(again.tombstones) == 2 and again.tombstones[0]["deleted_revision"] == "4"


def test_noop_bootstrap_and_deep_immutability() -> None:
    definition = graph()
    seed = BootstrapCandidate.own(definition)
    assert seed.definition is definition and not hasattr(seed, "revision")
    with pytest.raises(GraphError, match="Revision zero"):
        RevisionState(definition, "0")
    base = RevisionState(definition, "1")
    assert resolve_facts(base, graph()).noop
    assert replay(base, graph()) is base
    with pytest.raises(FrozenInstanceError):
        base.revision = "2"  # type: ignore[misc]
    change = resolve_facts(base, graph(node("r", description="updated")))
    with pytest.raises(TypeError):
        change.facts[0]["type"] = "x"  # type: ignore[index]
    assert facts_digest(change.facts) == canonical_digest(change.facts, "contexture.facts/v1")
    with pytest.raises(GraphError):
        apply_facts(base, change.facts, tombstones=[], revision="2", before_digest=base.definition_digest,
                    after_digest=change.candidate.digest(), resolved_fact_digest="sha256:" + "0" * 64)


def test_publication_final_positions_and_presentations() -> None:
    def pub(identity: str) -> dict[str, Any]:
        return {"kind": "prompt", "publication_id": identity, "name": identity, "description": identity,
                "opens_id": "r", "model_may_open": True}
    presentations = {"default_locale": "en", "catalogs": [{"locale": "en", "entries": [
        {"node_id": "r", "title": "before"}]}]}
    base = RevisionState(graph(publications=[pub("p"), pub("q"), pub("z")], presentations=presentations), "1")
    presentations["catalogs"][0]["entries"][0] = {"node_id": "r", "description": "after"}
    candidate = graph(publications=[pub("z"), pub("p"), pub("new")], presentations=presentations)
    replay(base, candidate)
    replay(RevisionState(candidate, "2"), graph(publications=[],
        presentations={"default_locale": "en", "catalogs": [{"locale": "en", "entries": []}]}))


@pytest.mark.parametrize("field", ["default", "catalogs", "extensions", "application_name", "mode"])
def test_complete_graph_and_presentation_fields_replay(field: str) -> None:
    base = RevisionState(graph(), "1")
    wire: Any = base.definition.as_json()
    if field == "default":
        wire["presentations"]["default_locale"] = "fr"
    elif field == "catalogs":
        wire["presentations"]["catalogs"] = [{"locale": "fr", "entries": []}]
    elif field == "extensions":
        wire["extensions"] = {"test:metadata": None}
    elif field == "mode":
        wire["mode"] = "runtime"
    else:
        wire[field] = "changed"
    replay(base, GraphDefinition.from_json(wire))


def test_replay_calls_no_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    base = RevisionState(graph(), "1")
    candidate = graph(node("r", name="new-name"))
    change = resolve_facts(base, candidate)
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Replay attempted an external effect")
    import builtins
    import socket
    import subprocess
    import time
    import uuid
    with monkeypatch.context() as patch:
        for owner, name in [(builtins, "open"), (socket, "socket"), (subprocess, "Popen"),
                            (time, "time"), (uuid, "uuid4")]:
            patch.setattr(owner, name, forbidden)
        result = apply_facts(base, change.facts, tombstones=[], revision="2",
            before_digest=base.definition_digest, after_digest=candidate.digest())
    assert canonical_json(result.definition.as_json()) == canonical_json(candidate.as_json())


def test_delete_reference_closure_is_one_atomic_patch() -> None:
    r = node("r", members={"children": ["a"]}, uses=["a"])
    r.pop("instructions")
    r["instruction_template"] = {"parts": [{"kind": "NodeRef", "target_id": "a"}]}
    base = RevisionState(graph(r, node("a"), publications=[{
        "kind": "prompt", "publication_id": "p", "name": "p", "description": "p",
        "opens_id": "a", "model_may_open": True}], presentations={"default_locale": "en",
        "catalogs": [{"locale": "en", "entries": [{"node_id": "a", "title": "translated"}]}]}), "7")
    candidate = graph(presentations={"default_locale": "en", "catalogs": [{"locale": "en", "entries": []}]})
    change = resolve_facts(base, candidate)
    assert {str(f["type"]) for f in change.facts} == {"NodeFieldsSet", "MembersAssigned", "UsesAssigned",
        "PublicationRemoved", "PresentationsAssigned", "NodeDeleted"}
    replay(base, candidate)
    for i in range(len(change.facts)):
        facts = list(change.facts)
        facts.pop(i)
        with pytest.raises(GraphError):
            apply_facts(base, facts, tombstones=change.tombstones, revision="8",
                        before_digest=base.definition_digest, after_digest=candidate.digest())


def test_roots_members_singletons_and_uses_order() -> None:
    base = RevisionState(graph(node("r", members={"pre_process": "pre", "children": ["a", "b"]}),
        node("pre", role_variant="pre_process"), node("a", uses=["b", "pre"]), node("b")), "2")
    candidate = graph(node("r", members={"children": ["b"]}), node("pre", role_variant="pre_process"),
        node("a", uses=["pre", "b"]), node("b"), roots=[{"node_id": "a", "plane": "prompt"},
        {"node_id": "r", "plane": "model"}, {"node_id": "pre", "plane": "model"}])
    replay(base, candidate)


def test_runtime_tool_all_fields_and_pinned_resource_evidence() -> None:
    from contexture.core.graph.model import BindingDescriptor, PublicationContract
    binding = {"kind": "registered", "binding_id": "reader", "version": "1",
               "portability": {"kind": "portable", "contract_id": "reader-v1"}}
    tool = {"node_id": "t", "kind": "tool", "name": "t", "description": "t", "binding": binding}
    base = RevisionState(graph(node("r", members={"tools": ["t"]}), tool, mode="runtime"), "1")
    wire: Any = base.definition.as_json()
    t = next(n for n in wire["nodes"] if n["node_id"] == "t")
    t["binding"]["config"] = {"x": None}
    t["binding"]["secret_refs"] = ["secret-handle"]
    t["input_contract"] = False
    candidate = GraphDefinition.from_json(wire)
    replay(base, candidate)
    contracts = {"t": PublicationContract(BindingDescriptor.from_json(t["binding"]), "read_only", True)}
    wire["publications"] = [{"kind": "resource", "publication_id": "p", "name": "resource",
                            "uri": "urn:test:resource", "mime_type": "text/plain", "opens_id": "t"}]
    with_resource = GraphDefinition.from_json(wire, publication_contracts=contracts)
    change = resolve_facts(RevisionState(candidate, "2"), with_resource)
    event = change_event(RevisionState(candidate, "2"), change, command_digest="sha256:" + "1" * 64, reason="test")
    oracle(event, "Event")
    # Replay is a pure function of the accepted journal: no registry needed.
    result = apply_event(RevisionState(candidate, "2"), event, tombstones=[])
    assert result.definition_digest == with_resource.digest()
    # A NEW candidate still requires current pinned registry evidence.
    with pytest.raises(GraphError) as error:
        GraphDefinition.from_json(wire)
    assert error.value.code == "binding_contract_required"


@pytest.mark.parametrize("mutation", ["policy", "kind", "revision", "remove", "created", "duplicate"])
def test_tombstone_corruption_rejected(mutation: str) -> None:
    base = RevisionState(graph(node("r", members={"children": ["a"]}), node("a", mutable=False)), "1")
    deleted = replay(base, graph())
    change = resolve_facts(deleted, base.definition)
    facts: Any = thaw_json(change.facts)
    tombstones: Any = thaw_json(change.tombstones)
    if mutation == "policy":
        facts[0]["tombstone"]["mutable"] = True
    elif mutation == "kind":
        facts[0]["tombstone"]["kind"] = "skill"
    elif mutation == "revision":
        facts[0]["tombstone"]["deleted_revision"] = "1"
    elif mutation == "remove":
        tombstones.clear()
    elif mutation == "created":
        facts[0]["type"] = "NodeCreated"
        facts[0].pop("tombstone")
    else:
        tombstones.append(deepcopy(tombstones[0]))
    with pytest.raises(GraphError):
        apply_facts(deleted, facts, tombstones=tombstones, revision="3",
                    before_digest=deleted.definition_digest, after_digest=base.definition_digest)


@pytest.mark.parametrize("change", ["entry-order", "catalog-order", "default"])
def test_all_presentation_orders_are_exact(change: str) -> None:
    presentations: Any = {"default_locale": "en", "catalogs": [{"locale": "en", "entries": [
        {"node_id": "r", "title": "root"}, {"node_id": "a", "description": "child"}]},
        {"locale": "fr", "entries": []}]}
    base = RevisionState(graph(node("r", members={"children": ["a"]}), node("a"), presentations=presentations), "1")
    if change == "entry-order":
        presentations["catalogs"][0]["entries"].reverse()
    elif change == "catalog-order":
        presentations["catalogs"].reverse()
    else:
        presentations["default_locale"] = "fr"
    candidate = graph(node("r", members={"children": ["a"]}), node("a"), presentations=presentations)
    replay(base, candidate)


def test_counter_exhaustion_and_noop_at_maximum() -> None:
    base = RevisionState(graph(), "99999999999999999999")
    assert replay(base, graph()) is base
    change = resolve_facts(base, graph(node("r", name="new-name")))
    with pytest.raises(GraphError) as error:
        change_event(base, change, command_digest="sha256:" + "1" * 64, reason="test")
    assert error.value.code == "limit_exceeded"


def test_transient_create_delete_has_no_tombstone_and_normalization_is_deterministic() -> None:
    base = RevisionState(graph(), "1")
    # Facts consume final candidates, not a transient allocation/command log.
    assert resolve_facts(base, graph()).facts == ()
    assert resolve_facts(base, graph()).tombstones == ()
    wire: Any = graph(node("r", members={"children": ["a", "b"]}), node("a"), node("b")).as_json()
    a = GraphDefinition.from_json(wire)
    wire["nodes"].reverse()
    b = GraphDefinition.from_json(wire)
    assert resolve_facts(base, a).facts == resolve_facts(base, b).facts


def test_durable_event_is_only_json_and_replay_rejects_final_containment_cycle() -> None:
    base = RevisionState(graph(node("r", members={"children": ["a"]}), node("a")), "1")
    candidate = graph(node("r", members={"children": ["a"]}), node("a", description="changed"))
    change = resolve_facts(base, candidate)
    event = change_event(base, change, command_digest="sha256:" + "1" * 64, reason="test")
    # Ordinary JSON serialization in another process/language needs no class
    # names, pickle, callbacks, handlers or executable schema objects.
    durable = json.loads(json.dumps(thaw_json(event), ensure_ascii=False))
    retained = json.loads(json.dumps(thaw_json(change.tombstones)))
    assert apply_event(base, durable, tombstones=retained).definition_digest == candidate.digest()
    facts: Any = thaw_json(change.facts)
    facts.append({"type": "MembersAssigned", "node_id": "a",
        "before": {"children": [], "skills": [], "tools": []},
        "after": {"children": ["r"], "skills": [], "tools": []}})
    with pytest.raises(GraphError):
        apply_facts(base, facts, tombstones=[], revision="2", before_digest=base.definition_digest,
                    after_digest=candidate.digest())


def test_real_bootstrap_has_null_preimage_and_nonempty_revision_one() -> None:
    base = UnseededState("g")
    assert base.revision == "0" and not hasattr(base, "definition")
    assert base.definition_digest == canonical_digest(None, "contexture.graph/v1")
    event = bootstrap_event(base, BootstrapCandidate.own(graph()), command_digest="sha256:" + "1" * 64,
                            reason="seed", approval_ref="verified-by-owner")
    oracle(event, "Event")
    result = apply_event(base, event, tombstones=[])
    assert result.revision == "1" and result.definition.nodes.keys() == {"r"}
    assert result.tombstones == ()
    with pytest.raises(GraphError):
        apply_event(result, event, tombstones=[])


@pytest.mark.parametrize("mutation", ["before", "revision", "identity", "null", "empty",
                                     "extra", "duplicate", "approval", "source", "tombstone"])
def test_bootstrap_exact_shape_and_preconditions(mutation: str) -> None:
    base = UnseededState("g")
    event: Any = thaw_json(bootstrap_event(base, graph(), command_digest="sha256:" + "1" * 64,
                                         reason="seed", approval_ref="a"))
    tombstones: Any = []
    if mutation == "before":
        event["before_digest"] = graph().digest()
    elif mutation == "revision":
        event["result_revision"] = "2"
    elif mutation == "identity":
        event["graph_id"] = "other"
    elif mutation == "null":
        event["resolved_facts"][0]["before"] = {}
    elif mutation == "empty":
        event["resolved_facts"][0]["after"]["nodes"] = []
    elif mutation == "extra":
        event["resolved_facts"][0]["ignored"] = True
    elif mutation == "duplicate":
        event["resolved_facts"] *= 2
    elif mutation == "approval":
        del event["migration"]["approval_ref"]
    elif mutation == "source":
        event["migration"]["source_digest"] = graph().digest()
    else:
        tombstones = [{"node_id": "old", "kind": "role", "mutable": True, "deleted_revision": "1"}]
    with pytest.raises(GraphError):
        apply_event(base, event, tombstones=tombstones)


def migration_case() -> tuple[RevisionState, GraphDefinition, Any, Any, Any]:
    from contexture.core.graph.diff import definition_diff
    base = RevisionState(graph(node("r", mutable=False)), "7", ({"node_id": "r", "kind": "role",
        "mutable": False, "deleted_revision": "3"},))
    candidate = graph(node("r", mutable=True, description="upgraded"), application_name="upgraded")
    exact = definition_diff(base.definition, candidate).as_json()
    mapping = {"retained_ids": ["r"], "new_ids": [], "deleted_ids": []}
    provenance = {"kind": "migration", "migration_id": "m", "approval_ref": "approval",
                  "source_digest": base.definition_digest, "exact_diff": exact}
    return base, candidate, exact, mapping, provenance


def test_trusted_migration_exact_diff_provenance_and_retained_policy() -> None:
    base, candidate, exact, mapping, provenance = migration_case()
    with pytest.raises(GraphError):
        resolve_facts(base, candidate)
    change = resolve_migration(base, candidate, migration_id="m", exact_diff=exact, source_mapping=mapping)
    assert change.facts[0]["type"] == "SemanticMigrated"
    assert change.tombstones[0]["mutable"] is True
    event = change_event(base, change, command_digest="sha256:" + "1" * 64,
                         reason="upgrade", migration=provenance)
    oracle(event, "Event")
    result = apply_event(base, event, tombstones=change.tombstones)
    assert result.revision == "8" and result.definition_digest == candidate.digest()
    assert result.tombstones == change.tombstones
    assert base.tombstones[0]["mutable"] is False


@pytest.mark.parametrize("mutation", ["approval", "source", "diff", "mapping", "before", "after",
                                     "missing", "order", "no-provenance", "id", "extra"])
def test_trusted_migration_rejects_corruption(mutation: str) -> None:
    base, candidate, exact, mapping, provenance = migration_case()
    change = resolve_migration(base, candidate, migration_id="m", exact_diff=exact, source_mapping=mapping)
    event: Any = thaw_json(change_event(base, change, command_digest="sha256:" + "1" * 64,
                                       reason="upgrade", migration=provenance))
    if mutation == "approval":
        del event["migration"]["approval_ref"]
    elif mutation == "source":
        event["migration"]["source_digest"] = candidate.digest()
    elif mutation == "diff":
        event["migration"]["exact_diff"]["nodes"] = []
    elif mutation == "mapping":
        event["resolved_facts"][0]["source_mapping"]["retained_ids"] = []
    elif mutation in ("before", "after"):
        event["resolved_facts"][1][mutation] = "inherit"
    elif mutation == "missing":
        event["resolved_facts"].pop(0)
    elif mutation == "order":
        event["resolved_facts"].reverse()
    elif mutation == "no-provenance":
        del event["migration"]
    elif mutation == "id":
        event["migration"]["migration_id"] = "other"
    else:
        event["migration"]["bypass"] = True
    with pytest.raises(GraphError):
        apply_event(base, event, tombstones=change.tombstones)


def test_presentation_all_order_empty_catalog_normalization_and_corruption() -> None:
    base = RevisionState(graph(node("r", members={"children": ["a"]}), node("a"),
        presentations={"default_locale": "en", "catalogs": [{"locale": "en", "entries": [
            {"node_id": "r", "title": "R"}, {"node_id": "a", "title": "A"}]},
            {"locale": "fr", "entries": []}]}), "1")
    candidate = graph(node("r", members={"children": ["a"]}), node("a"), presentations={
        "default_locale": "ZH-hans-cn", "catalogs": [{"locale": "x-empty", "entries": []},
        {"locale": "EN", "entries": [{"node_id": "a", "title": "A"}, {"node_id": "r", "title": "R"}]}]})
    assert candidate.presentations.default_locale == "zh-Hans-CN"
    change = resolve_facts(base, candidate)
    assert len(change.facts) == 1 and change.facts[0]["type"] == "PresentationsAssigned"
    replay(base, candidate)
    for field in ("before", "after"):
        facts: Any = thaw_json(change.facts)
        facts[0][field]["catalogs"].reverse()
        with pytest.raises(GraphError):
            apply_facts(base, facts, tombstones=[], revision="2", before_digest=base.definition_digest,
                        after_digest=candidate.digest())


def test_trusted_resolver_rejects_inexact_diff_and_kind_identity_rewrite() -> None:
    base, candidate, exact, mapping, _ = migration_case()
    with pytest.raises(GraphError):
        resolve_migration(base, candidate, migration_id="m", exact_diff={}, source_mapping=mapping)
    with pytest.raises(GraphError):
        resolve_migration(base, candidate, migration_id="m", exact_diff=exact, source_mapping={})
    wire: Any = graph().as_json()
    wire["nodes"][0]["kind"] = "skill"
    del wire["nodes"][0]["members"]
    del wire["nodes"][0]["role_variant"]
    from contexture.core.graph.diff import definition_diff
    changed = GraphDefinition.from_json(wire)
    for resolve in (lambda: resolve_facts(base, changed), lambda: resolve_migration(
            base, changed, migration_id="m", exact_diff=definition_diff(base.definition, changed).as_json(),
            source_mapping=mapping)):
        with pytest.raises(GraphError):
            resolve()


@pytest.mark.parametrize("mutation", ["before", "omit", "extra", "duplicate", "identity"])
def test_graph_metadata_facts_exact_changed_key_set(mutation: str) -> None:
    base = RevisionState(graph(), "1")
    candidate = graph(application_name="changed", extensions={"test:null": None})
    change = resolve_facts(base, candidate)
    facts: Any = thaw_json(change.facts)
    assert facts[0]["type"] == "GraphFieldsSet"
    if mutation == "before":
        facts[0]["before"]["application_name"] = "forged"
    elif mutation == "omit":
        del facts[0]["after"]["extensions"]
    elif mutation == "extra":
        facts[0]["before"]["mode"] = facts[0]["after"]["mode"] = "disclosure_only"
    elif mutation == "duplicate":
        facts *= 2
    else:
        facts[0]["after"]["graph_id"] = "other"
    with pytest.raises(GraphError):
        apply_facts(base, facts, tombstones=[], revision="2", before_digest=base.definition_digest,
                    after_digest=candidate.digest())


def test_bootstrap_and_migration_replay_have_no_external_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import socket
    import time
    import uuid
    base, candidate, exact, mapping, provenance = migration_case()
    change = resolve_migration(base, candidate, migration_id="m", exact_diff=exact, source_mapping=mapping)
    event = change_event(base, change, command_digest="sha256:" + "1" * 64,
                         reason="upgrade", migration=provenance)
    unseeded = UnseededState("g")
    seed = bootstrap_event(unseeded, graph(), command_digest="sha256:" + "1" * 64,
                           reason="seed", approval_ref="a")
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Pure replay executed an external effect")
    import contexture.core.graph.policy as policy
    with monkeypatch.context() as patch:
        for owner, name in [(builtins, "open"), (socket, "socket"), (time, "time"),
                            (uuid, "uuid4"), (policy, "check_admission"), (policy, "protect_change")]:
            patch.setattr(owner, name, forbidden)
        assert apply_event(unseeded, seed, tombstones=[]).revision == "1"
        assert apply_event(base, event, tombstones=change.tombstones).revision == "8"