"""Bounded internal disclosure evidence, not Host/wire acceptance or G1."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from typing import Any

import pytest

from contexture.application.managed_lowering import lower_static_index
from contexture.core.graph.bindings import (
    AppDependencyContract, BindingRegistration, RegistryGeneration,
)
from contexture.core.graph.compiler import GraphCompiler, StructuralPlan
from contexture.core.graph.disclosure import ManagedDisclosure
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import BindingDescriptor, GraphDefinition, Portability
from contexture.core.graph.references import format_reference, parse_reference
from contexture.core.model import ControllerManager, PostProcess, PreProcess, Role, Skill, Tool
from contexture.core.model.disclosure import Disclosure
from contexture.core.model.index import Index
from contexture.core.model.system_api import Refused, SystemAPI

GENERATION = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
NO_EXECUTION = "sha256:" + "e" * 64
PORTABLE = {"kind": "portable", "contract_id": "primitive-v1"}
CALLS: list[str] = []


def forbidden(*args: object, **kwargs: object) -> object:
    CALLS.append("called")
    raise AssertionError("Disclosure executed a callback or queried an author object")


class Reader(Tool):
    def __init__(self) -> None:
        super().__init__(name="read", description="Read", read_only=True,
                         uses=("ops/method",))

    async def invoke(self) -> object:
        return forbidden()


class Operator(Role):
    def __init__(self) -> None:
        super().__init__(name="ops", description="Operate", instructions="  literal /ops/read\n",
            pre_process=PreProcess(name="prepare", description="Prepare", instructions="Before"),
            children=[Role(name="branch", description="Branch", instructions="Branch",
                children=[Role(name="deep", description="Deep", instructions="Deeper")])],
            post_process=PostProcess(name="finish", description="Finish", instructions="After"),
            skills=[Skill(name="method", description="Method", instructions="Method\n",
                          uses=("ops/read",))], tools=[Reader()])


def registration(*, effect: str = "read_only", schema: Any = None) -> BindingRegistration:
    return BindingRegistration(binding_id="reader", version="1", kind="registered",
        portability=Portability.from_json(PORTABLE), effect=effect, abi="call-v1",  # type: ignore[arg-type]
        artifact_digest="sha256:" + "a" * 64, input_schema={} if schema is None else schema,
        validator_contract_id="validator-v1", decoder_contract_id="decoder-v1",
        default_contract_id="default-v1", coercion_contract_id="coercion-v1",
        handler=forbidden, validator=forbidden, decoder=forbidden, factory=forbidden,
        argument_free=True)


def compile_graph(raw: dict[str, Any], *, locale: str = "en",
                  pinned: RegistryGeneration | None = None) -> StructuralPlan:
    evidence = {} if pinned is None else {
        n["node_id"]: pinned.resolve(BindingDescriptor.from_json(n["binding"])).publication_contract
        for n in raw["nodes"] if "binding" in n}
    definition = GraphDefinition.from_json(raw, publication_contracts=evidence)
    return GraphCompiler.compile(definition, registry=pinned, compiler_contract_version="compiler-v1",
        compiler_build="build-1", app_dependency_contract=AppDependencyContract("deps-v1", "deps-build"),
        environment_compatibility=("python-3.14",), locale=locale,
        no_execution_registry_digest=NO_EXECUTION if pinned is None else None)


def static_fixture(*, runtime: bool = True) -> tuple[Disclosure, StructuralPlan, dict[str, str], Any]:
    manager = ControllerManager()
    manager.register_role(Operator)
    manager.register_skill(Skill(name="command", description="Person", instructions="Person only"))
    index = Index.bound(manager) if runtime else Index.unbound(manager)
    ids = {ref: "id-" + str(i) for i, (ref, _) in enumerate(index.walk())}
    pinned = RegistryGeneration(build="registry-1", registrations=[registration()]) if runtime else None
    bindings = {ids["ops/read"]: BindingDescriptor.from_json({"kind": "registered",
        "binding_id": "reader", "version": "1", "portability": PORTABLE})} if runtime else None
    lowered = lower_static_index(index, graph_id="graph", application_name="Application",
        mode="runtime" if runtime else "disclosure_only", node_ids=ids,
        root_planes={"ops": "model", "command": "prompt"}, default_locale="en",
        bindings=bindings, registry=pinned)
    plan = GraphCompiler.compile(lowered.definition, registry=pinned,
        compiler_contract_version="compiler-v1", compiler_build="build-1",
        app_dependency_contract=AppDependencyContract("deps-v1", "deps-build"),
        environment_compatibility=("python-3.14",), locale="en",
        no_execution_registry_digest=NO_EXECUTION if pinned is None else None)
    return Disclosure(index=index, prompt_roots=frozenset({"command"})), plan, ids, lowered


def view(plan: StructuralPlan, **changes: Any) -> ManagedDisclosure:
    options: dict[str, Any] = dict(plan=plan, generation_uuid=GENERATION,
        authorized_ids=frozenset(plan.index.preorder), current_view_evidence="policy-view-7")
    return ManagedDisclosure(**(options | changes))


def ref(plan: StructuralPlan, node_id: str) -> str:
    return format_reference(plan.definition.graph_id, GENERATION, node_id)


def normalize(value: Any, plan: StructuralPlan, ids: dict[str, str]) -> Any:
    """Normalize only returned canonical refs (including framework contracts)."""
    if isinstance(value, dict):
        return {k: normalize(v, plan, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, plan, ids) for v in value]
    if isinstance(value, str):
        for path, identity in ids.items():
            value = value.replace(ref(plan, identity), path)
    return value


def route_only_uses(value: Any) -> Any:
    """Explicit managed delta: legacy Tool.card expands uses, managed never does.

    Compare real static output everywhere else; this is intentionally NOT a
    claim that the legacy recursive Tool.card overlay is byte-identical.
    """
    if isinstance(value, dict):
        return {key: ([{k: v for k, v in card.items()
                        if k in {"kind", "name", "title", "description", "ref"}}
                       for card in item] if key == "uses" else route_only_uses(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [route_only_uses(item) for item in value]
    return value


@pytest.mark.parametrize("runtime", [True, False])
def test_real_static_lowering_semantic_oracle_all_levels(runtime: bool) -> None:
    static, plan, ids, lowered = static_fixture(runtime=runtime)
    managed = view(plan)
    # Literal inventory remains unreviewed: this is not migration acceptance.
    assert lowered.literal_inventory and all(i.status == "unreviewed" for i in lowered.literal_inventory)
    assert normalize(managed.discover(), plan, ids) == static.skeleton()
    paths = ["ops/read", "ops", "ops/method", "ops/branch", "ops/prepare", "ops/finish"]
    assert normalize(managed.inspect([ref(plan, ids[p]) for p in paths]), plan, ids) == static.inspect(paths)
    for path in paths:
        actual = normalize(managed.open(ref(plan, ids[path])), plan, ids)
        assert actual == route_only_uses(static.open(path))
        assert (normalize(managed.route(ref(plan, ids[path])), plan, ids)
            == static.routing_card_of(static.find(path)))
    active = managed.open(ref(plan, ids["ops"]))
    assert [c["name"] for c in active["roles"]] == ["prepare", "branch", "finish"]
    assert "deep" not in json.dumps(active)
    assert "does not execute" in active["instructions"]
    assert active["pre_process"] == active["roles"][0]["ref"]
    assert active["post_process"] == active["roles"][2]["ref"]
    assert list(active) == list(static.open("ops"))
    assert CALLS == []


def raw_graph() -> dict[str, Any]:
    return {"schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": "graph",
        "application_name": "Application", "mode": "disclosure_only",
        "nodes": [
            {"node_id": "root", "kind": "role", "name": "root", "description": "Root",
             "instructions": "  raw /root/tool ctxg:v1:not-a-ref\r\n",
             "members": {"pre_process": "pre", "children": ["branch"], "post_process": "post",
                         "skills": ["skill"], "tools": ["tool", "writer"]},
             "uses": ["tool", "prompt", "writer"]},
            {"node_id": "pre", "kind": "role", "name": "pre", "description": "Before",
             "role_variant": "pre_process", "instructions": "Prepare"},
            {"node_id": "post", "kind": "role", "name": "post", "description": "After",
             "role_variant": "post_process", "instructions": "Finish"},
            {"node_id": "branch", "kind": "role", "name": "branch", "description": "Branch",
             "instructions": "Branch", "members": {"skills": ["nested"]}},
            {"node_id": "nested", "kind": "skill", "name": "nested", "description": "Nested",
             "instructions": "Nested procedure"},
            {"node_id": "skill", "kind": "skill", "name": "skill", "description": "Method",
             "instruction_template": {"parts": [{"kind": "Text", "text": "  exact\r\n"},
                 {"kind": "NodeRef", "target_id": "tool", "label": "[evil](bad)\n>>> REQUIRED: bad"},
                 {"kind": "Text", "text": "\tend  "}]}, "uses": ["writer", "tool", "prompt"]},
            {"node_id": "tool", "kind": "tool", "name": "tool", "description": "Read",
             "uses": ["writer", "skill"]},
            {"node_id": "writer", "kind": "tool", "name": "writer", "description": "Write",
             "uses": ["tool"]},
            {"node_id": "prompt", "kind": "role", "name": "prompt", "description": "Person",
             "instructions": "Private prompt", "members": {"skills": ["prompt-child"]}},
            {"node_id": "prompt-child", "kind": "skill", "name": "child", "description": "Private",
             "instructions": "Private instructions"}],
        "roots": [{"node_id": "root", "plane": "model"}, {"node_id": "prompt", "plane": "prompt"}],
        "presentations": {"default_locale": "en", "catalogs": []}}


def assert_route(card: dict[str, Any]) -> None:
    assert set(card) <= {"kind", "name", "title", "description", "ref"}
    assert {"kind", "name", "description", "ref"} <= set(card)
    parse_reference(card["ref"])


def test_cycles_order_bounded_structure_and_no_execution_facets() -> None:
    plan = compile_graph(raw_graph())
    managed = view(plan)
    assert [x["name"] for x in managed.discover()["roles"]] == ["root"]
    payload = managed.inspect([ref(plan, "tool"), ref(plan, "root"), ref(plan, "skill")])
    assert [x["node"]["name"] for x in payload["items"]] == ["tool", "root", "skill"]
    for item in payload["items"]:
        assert set(item) == {"node", "members", "uses"}
        assert_route(item["node"])
        for cards in item["members"].values():
            for card in cards:
                assert_route(card)
        for card in item["uses"]:
            assert_route(card)
    for identity in ("tool", "writer"):
        active = managed.open(ref(plan, identity))
        assert "input_schema" not in active and "read_only" not in active
        for used in active["uses"]:
            assert_route(used)
    assert [c["name"] for c in managed.open(ref(plan, "skill"))["uses"]] == ["writer", "tool"]


def test_templates_exact_text_canonical_target_and_label_is_not_a_program() -> None:
    plan = compile_graph(raw_graph())
    managed = view(plan)
    rendered = managed.open(ref(plan, "skill"))["instructions"]
    assert rendered == "  exact\r\n" + repr(ref(plan, "tool")) + "\tend  "
    assert "evil" not in rendered and "REQUIRED" not in rendered
    branch = managed.open(ref(plan, "branch"))
    assert branch["instructions"] == "Branch"  # no inherited process
    raw = raw_graph()
    raw["nodes"][0]["members"].pop("pre_process")
    raw["nodes"][0]["members"].pop("post_process")
    raw["nodes"][0]["members"]["children"] += ["pre", "post"]
    plain = compile_graph(raw)
    assert view(plain).open(ref(plain, "root"))["instructions"] == raw["nodes"][0]["instructions"]


@pytest.mark.parametrize("hidden", ["tool", "pre", "post"])
def test_hidden_typed_target_or_designated_process_refuses_without_leak(hidden: str) -> None:
    plan = compile_graph(raw_graph())
    managed = view(plan, authorized_ids=set(plan.index.preorder) - {hidden})
    target = "skill" if hidden == "tool" else "root"
    with pytest.raises(GraphError) as failure:
        managed.open(ref(plan, target))
    assert failure.value.code == "permission_denied"
    assert hidden not in json.dumps(failure.value.to_dict())
    assert failure.value.diagnostics == ()
    # Non-activating levels expose neither the hidden card nor the process program.
    output = json.dumps(managed.inspect([ref(plan, "root"), ref(plan, "skill")]))
    assert ref(plan, hidden) not in output
    assert '"instructions"' not in output and '"pre_process"' not in output


def test_no_hidden_cards_uses_digest_counts_and_current_pinned_intersection() -> None:
    plan = compile_graph(raw_graph())
    ceiling = {"root", "branch", "nested", "skill", "pre", "post", "deleted-current-id"}
    managed = view(plan, authorized_ids=ceiling)
    inspected = managed.inspect([ref(plan, "root"), ref(plan, "skill")])
    encoded = json.dumps(inspected)
    for hidden in ("tool", "writer", "prompt", "prompt-child", "deleted-current-id"):
        assert ref(plan, hidden) not in encoded
    assert "digest" not in encoded and "count" not in encoded
    assert inspected["items"][0]["members"]["tools"] == []
    assert inspected["items"][0]["uses"] == []
    assert managed.route(ref(plan, "root"))["name"] == "root"
    with pytest.raises(GraphError):
        managed.route(ref(plan, "tool"))
    assert view(plan, authorized_ids=set()).discover() == {"roles": [], "skills": [], "tools": []}


def test_selected_subtree_projected_roots_and_preorder_not_anchor_set_order() -> None:
    plan = compile_graph(raw_graph())
    managed = view(plan, selection_anchors={"writer", "nested", "branch", "pre"})
    result = managed.discover()
    assert [c["name"] for c in result["roles"]] == ["pre", "branch"]
    assert result["skills"] == []  # nested is reached through branch, not duplicated
    assert [c["name"] for c in result["tools"]] == ["writer"]
    assert managed.open(ref(plan, "branch"))["skills"][0]["name"] == "nested"
    with pytest.raises(GraphError):
        managed.open(ref(plan, "root"))
    assert view(plan, selection_anchors=set()).discover()["roles"] == []
    with pytest.raises(GraphError):
        view(plan, selection_anchors={"not-pinned"})


def test_prompt_plane_person_door_and_reserved_static_rule_no_invented_flag() -> None:
    raw = raw_graph()
    raw["publications"] = [{"kind": "prompt", "publication_id": "manual", "name": "manual",
        "description": "Person starts", "opens_id": "branch", "model_may_open": False}]
    plan = compile_graph(raw)
    managed = view(plan)
    branch_card = managed.route(ref(plan, "branch"))
    assert_route(branch_card)
    assert "openable" not in branch_card
    assert any(c["name"] == "branch" for c in managed.open(ref(plan, "root"))["roles"])
    for method in (lambda: managed.open(ref(plan, "branch")),
                   lambda: managed.inspect([ref(plan, "branch")]),
                   lambda: managed.route(ref(plan, "prompt-child"))):
        with pytest.raises(GraphError) as failure:
            method()
        assert failure.value.code == "permission_denied"
    # Reservation is exact-target, not a subtree prohibition.
    assert managed.open(ref(plan, "nested"))["instructions"] == "Nested procedure"
    person = view(plan, audience="person")
    assert person.open(ref(plan, "branch"))["instructions"] == "Branch"
    assert person.open(ref(plan, "prompt-child"))["instructions"] == "Private instructions"
    assert [c["name"] for c in person.discover()["roles"]] == ["root", "prompt"]


def test_atomic_inspect_prevalidation_before_any_render(monkeypatch: Any) -> None:
    plan = compile_graph(raw_graph())
    managed = view(plan)
    monkeypatch.setattr(ManagedDisclosure, "_route", forbidden)
    for refs in ([ref(plan, "root"), ref(plan, "prompt")],
                 [ref(plan, "root"), "/root"], [ref(plan, "root"), ref(plan, "root")],
                 [ref(plan, "root"), format_reference("graph", OTHER, "skill")]):
        with pytest.raises(GraphError):
            managed.inspect(refs)
    assert CALLS == []


@pytest.mark.parametrize("bad", ["/root", "root", "ctxg:v1:Z3JhcGg=:" + GENERATION + ":cm9vdA",
    "ctxg:v1:Z3JhcGg:" + GENERATION + ":cm9vdB", "ctxg:v1:Z3JhcGg:" + GENERATION + ":cm9vdA:extra"])
def test_alias_and_noncanonical_refs_never_fall_back_to_path(bad: str) -> None:
    plan = compile_graph(raw_graph())
    managed = view(plan)
    for operation in (managed.route, managed.open, lambda r: managed.inspect([r])):
        with pytest.raises(GraphError) as failure:
            operation(bad)
        assert failure.value.code == "schema_invalid"


def test_wrong_graph_stale_and_hidden_identity_are_bounded_errors() -> None:
    plan = compile_graph(raw_graph())
    for reference, code in ((format_reference("other", GENERATION, "root"), "stale_reference"),
                            (format_reference("graph", OTHER, "root"), "stale_reference"),
                            (ref(plan, "missing-secret"), "permission_denied")):
        with pytest.raises(GraphError) as failure:
            view(plan).open(reference)
        assert failure.value.code == code
        assert reference not in str(failure.value) and "missing-secret" not in str(failure.value)


def test_locale_comes_from_compiled_plan_and_does_not_change_instruction_or_ref() -> None:
    raw = raw_graph()
    raw["presentations"] = {"default_locale": "en", "catalogs": [
        {"locale": "en", "entries": [{"node_id": "branch", "title": "Default"}]},
        {"locale": "zh-Hant", "entries": [{"node_id": "branch", "title": "分支",
                                           "description": "本地描述"}]}]}
    localized = compile_graph(raw, locale="zh-hant-TW")
    fallback = compile_graph(raw, locale="fr-CA")
    actual = view(localized).open(ref(localized, "branch"))
    default = view(fallback).open(ref(fallback, "branch"))
    assert actual["title"] == "分支" and actual["description"] == "本地描述"
    assert default["title"] == "Default" and default["description"] == "Branch"
    assert actual["name"] == "branch" and actual["ref"] == default["ref"]
    assert actual["instructions"] == default["instructions"] == "Branch"
    assert "title" not in view(localized).route(ref(localized, "tool"))


@pytest.mark.parametrize("effect", ["read_only", "write"])
def test_pinned_schema_effect_runtime_tools_and_argumentfree_structural_levels(effect: str) -> None:
    raw = raw_graph()
    raw["mode"] = "runtime"
    schema = {"type": "object", "properties": {"hint": {"type": "string"}}, "additionalProperties": False}
    pinned = RegistryGeneration(build="registry-1", registrations=[registration(effect=effect, schema=schema)])
    for node in raw["nodes"]:
        if node["kind"] == "tool":
            node["binding"] = {"kind": "registered", "binding_id": "reader", "version": "1",
                               "portability": PORTABLE}
    plan = compile_graph(raw, pinned=pinned)
    managed = view(plan)
    schema["properties"]["hint"]["type"] = "integer"
    for identity in ("tool", "writer"):
        assert_route(managed.route(ref(plan, identity)))
        inspected = managed.inspect([ref(plan, identity)])["items"][0]
        assert_route(inspected["node"])
        assert inspected["members"] == {"roles": [], "skills": [], "tools": []}
        active = managed.open(ref(plan, identity))
        assert active["read_only"] is (effect == "read_only")
        assert active["input_schema"]["properties"]["hint"]["type"] == "string"
        for used in active["uses"]:
            assert_route(used)
    for held in managed.open(ref(plan, "root"))["tools"]:
        assert "input_schema" in held
        assert all("input_schema" not in u for u in held["uses"])
    assert CALLS == []


def test_no_author_queries_no_registry_resolution_no_handler_validator_decoder_factory(monkeypatch: Any) -> None:
    _, plan, ids, _ = static_fixture()
    CALLS.clear()
    for cls in (Role, Skill, Tool):
        monkeypatch.setattr(cls, "compile", forbidden)
        monkeypatch.setattr(cls, "members", forbidden)
    monkeypatch.setattr(RegistryGeneration, "resolve", forbidden)
    managed = view(plan)
    managed.discover()
    for path, identity in ids.items():
        if path == "command":
            continue
        managed.route(ref(plan, identity))
        managed.inspect([ref(plan, identity)])
        managed.open(ref(plan, identity))
    assert CALLS == []


def test_immutable_view_defensive_json_and_no_cache_leakage() -> None:
    _, plan, ids, _ = static_fixture()
    ceiling = set(plan.index.preorder)
    anchors = {ids["ops"]}
    managed = view(plan, authorized_ids=ceiling, selection_anchors=anchors)
    ceiling.clear()
    anchors.clear()
    expected = managed.open(ref(plan, ids["ops"]))
    payload = managed.open(ref(plan, ids["ops"]))
    payload["tools"][0]["input_schema"]["injected"] = True
    payload["roles"].clear()
    payload["tools"][0]["uses"][0]["name"] = "tampered"
    assert managed.open(ref(plan, ids["ops"])) == expected
    assert view(plan).open(ref(plan, ids["ops"])) == expected
    pinned_schema = plan.bindings[ids["ops/read"]].entry.input_schema
    assert not isinstance(pinned_schema, bool) and "injected" not in pinned_schema
    with pytest.raises(FrozenInstanceError):
        managed.generation_uuid = OTHER  # type: ignore[misc]
    inspected = managed.inspect([ref(plan, ids["ops"])])
    inspected["items"][0]["members"]["roles"].clear()
    assert managed.inspect([ref(plan, ids["ops"])])["items"][0]["members"]["roles"]


def test_explicit_bounds_no_silent_truncation_and_multibyte_response_budget() -> None:
    raw = raw_graph()
    for i in range(1100):
        identity = "wide-" + str(i)
        raw["nodes"].append({"node_id": identity, "kind": "tool", "name": identity, "description": "宽"})
        raw["nodes"][0]["members"]["tools"].append(identity)
    plan = compile_graph(raw)
    managed = view(plan)
    assert len(managed.discover()["roles"]) == 1  # not a flattened roster
    for operation in (lambda: managed.open(ref(plan, "root")),
                      lambda: managed.inspect([ref(plan, "root")])):
        with pytest.raises(GraphError) as failure:
            operation()
        assert failure.value.code == "too_large" and "paging" in str(failure.value)
    full = view(plan, max_cards=2000).open(ref(plan, "root"))
    assert len(full["tools"]) == 1102
    tiny = replace(managed, max_response_bytes=1)
    with pytest.raises(GraphError, match="byte limit"):
        tiny.discover()
    route = managed.route(ref(plan, "wide-0"))
    length = len(json.dumps(route, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert replace(managed, max_response_bytes=length).route(ref(plan, "wide-0")) == route
    with pytest.raises(GraphError):
        replace(managed, max_response_bytes=length-1).route(ref(plan, "wide-0"))
    for candidates in ([], [ref(plan, "root")] * 33, "not-a-sequence"):
        with pytest.raises(GraphError):
            managed.inspect(candidates)  # type: ignore[arg-type]


def test_required_current_evidence_generation_and_limits() -> None:
    plan = compile_graph(raw_graph())
    for changes in ({"current_view_evidence": ""}, {"generation_uuid": "not-a-uuid"},
                    {"max_cards": 0}, {"max_response_bytes": True}, {"max_inspect_refs": -1},
                    {"authorized_ids": ["root"]}, {"audience": "unknown"}):
        with pytest.raises(GraphError):
            view(plan, **changes)


def test_reserved_person_target_matches_real_static_api() -> None:
    static, plan, ids, _ = static_fixture()
    # Reserve the model root, not the unrelated prompt-only root.
    api = SystemAPI(tree=static, reserved=frozenset({"ops"}))
    raw = plan.definition.as_json()
    raw["publications"] = [{"kind": "prompt", "publication_id": "manual", "name": "manual",
        "description": "Person starts", "opens_id": ids["ops"], "model_may_open": False}]
    pinned = RegistryGeneration(build="registry-1", registrations=[registration()])
    reserved = compile_graph(raw, pinned=pinned)
    managed = view(reserved)
    assert normalize(managed.discover(), reserved, ids) == asyncio.run(api.discover())
    for action in (lambda: api.open("ops"), lambda: api.inspect(["ops"])):
        with pytest.raises(Refused):
            asyncio.run(action())
    for action in (lambda: managed.open(ref(reserved, ids["ops"])),
                   lambda: managed.inspect([ref(reserved, ids["ops"])])):
        with pytest.raises(GraphError):
            action()
    person = view(reserved, audience="person")
    assert normalize(person.open(ref(reserved, ids["ops"])), reserved, ids) == route_only_uses(
        asyncio.run(api.open_for_person("ops")))


@pytest.mark.parametrize("target,reserved", [("prompt", False), ("branch", True)])
def test_typed_reference_cannot_guess_prompt_or_activate_reserved_target(target: str, reserved: bool) -> None:
    raw = raw_graph()
    raw["nodes"][5]["instruction_template"]["parts"][1]["target_id"] = target
    if reserved:
        raw["publications"] = [{"kind": "prompt", "publication_id": "manual", "name": "manual",
            "description": "Person starts", "opens_id": target, "model_may_open": False}]
    plan = compile_graph(raw)
    with pytest.raises(GraphError) as failure:
        view(plan).open(ref(plan, "skill"))
    assert failure.value.code == "permission_denied"
    assert ref(plan, target) not in str(failure.value)
    assert repr(ref(plan, target)) in view(plan, audience="person").open(ref(plan, "skill"))["instructions"]


def test_discover_explicit_root_tools_are_route_only_not_active_cards() -> None:
    raw = raw_graph()
    raw["nodes"][0]["members"]["tools"].remove("writer")
    raw["roots"].insert(0, {"node_id": "writer", "plane": "model"})
    plan = compile_graph(raw)
    payload = view(plan).discover()
    assert len(payload["tools"]) == 1
    assert_route(payload["tools"][0])
    assert payload["tools"][0]["ref"] == ref(plan, "writer")
    assert "uses" not in payload["tools"][0]