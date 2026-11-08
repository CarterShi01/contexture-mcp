"""Bounded WP07 pure graph compilation, not a Host/disclosure wire oracle."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from contexture.core.graph.bindings import (
    AppDependencyContract, BindingRegistration, ExecutionManifest, RegistryGeneration,
)
from contexture.core.graph.compiler import GraphCompiler, ResolvedNodeRef
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import (
    BindingDescriptor, GraphDefinition, NodeRefPart, Portability, TextPart,
)
from contexture.core.graph.references import parse_reference

PORTABLE = {"kind": "portable", "contract_id": "primitive-v1"}
INPUT = {"type": "object", "additionalProperties": False}
GENERATION = "00000000-0000-4000-8000-000000000001"
OTHER_GENERATION = "00000000-0000-4000-8000-000000000002"
NO_EXECUTION = "sha256:" + "e" * 64  # Explicit test deployment attestation, not a sentinel.


def forbidden(*args: object, **kwargs: object) -> object:
    raise AssertionError("Compiler invoked business code")


def registration(binding_id: str = "reader", **changes: Any) -> BindingRegistration:
    fields: dict[str, Any] = dict(
        binding_id=binding_id, version="1", kind="registered",
        portability=Portability.from_json(PORTABLE), effect="read_only", abi="call-v1",
        artifact_digest="sha256:" + "a" * 64, input_schema=deepcopy(INPUT),
        validator_contract_id="validator-v1", decoder_contract_id="decoder-v1",
        default_contract_id="default-v1", coercion_contract_id="coercion-v1",
        handler=forbidden, validator=forbidden, decoder=forbidden, factory=forbidden,
        argument_free=True, allowed_config=("limit",), config_schema={"type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1}},
            "additionalProperties": False})
    return BindingRegistration(**(fields | changes))


def registry(*entries: BindingRegistration, build: str = "registry-1") -> RegistryGeneration:
    return RegistryGeneration(build=build, registrations=entries or [registration()])


def tool(identity: str = "t", binding_id: str = "reader") -> dict[str, Any]:
    return {"node_id": identity, "kind": "tool", "name": identity, "description": "Read",
        "binding": {"kind": "registered", "binding_id": binding_id, "version": "1",
                    "portability": deepcopy(PORTABLE)}, "input_contract": deepcopy(INPUT)}


def role(identity: str, **changes: Any) -> dict[str, Any]:
    return dict(node_id=identity, kind="role", name=identity,
                description="Original " + identity, instructions="literal /r/t ctxg:v1:not-a-ref") | changes


def raw_graph() -> dict[str, Any]:
    # Deliberately non-preorder and non-lexical node array, mixed root planes.
    return {"schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": "g",
        "application_name": "app", "mode": "runtime", "nodes": [tool("a"), role("p"),
            role("r", members={"pre_process": "pre", "children": ["child"],
                "post_process": "post", "skills": ["skill"], "tools": ["z", "a"]}),
            tool("z"), role("pre", role_variant="pre_process"),
            role("child", role_variant="pre_process"), role("post", role_variant="post_process"),
            {"node_id": "skill", "kind": "skill", "name": "skill", "description": "Skill",
             "instruction_template": {"parts": [{"kind": "Text", "text": "raw /r/z <>&[]"},
                {"kind": "NodeRef", "target_id": "z", "label": "[label](unsafe)\n"},
                {"kind": "NodeRef", "target_id": "p", "label": ""},
                {"kind": "NodeRef", "target_id": "a"}]} }],
        "roots": [{"node_id": "r", "plane": "model"}, {"node_id": "p", "plane": "prompt"}],
        "presentations": {"default_locale": "en", "catalogs": [
            {"locale": "en", "entries": [{"node_id": "r", "title": "Default title"}]},
            {"locale": "zh-Hant", "entries": [{"node_id": "r", "title": "Localized",
                "description": "Localized description"}]}]}}


def definition(raw: dict[str, Any] | None = None, reg: RegistryGeneration | None = None) -> GraphDefinition:
    value = raw_graph() if raw is None else raw
    pinned = reg or registry()
    evidence = {n["node_id"]: pinned.resolve(BindingDescriptor.from_json(n["binding"])).publication_contract
                for n in value["nodes"] if "binding" in n}
    return GraphDefinition.from_json(value, publication_contracts=evidence)


def compile_graph(value: GraphDefinition | None = None, **changes: Any) -> Any:
    options: dict[str, Any] = dict(registry=changes.get("registry") if "registry" in changes else registry(),
        compiler_contract_version="compiler-v1",
        compiler_build="compiler-build-1", app_dependency_contract=AppDependencyContract("deps-v1", "build-1"),
        environment_compatibility=("python-3.14",), locale="zh-hant-TW")
    return GraphCompiler.compile(value or definition(), **(options | changes))


def test_complete_index_and_preorder_resolution_not_node_array_or_lexical(monkeypatch: Any) -> None:
    raw = raw_graph()
    for node in raw["nodes"]:
        if node["kind"] == "tool":
            node["binding"]["binding_id"] = node["node_id"]
    pinned = registry(registration("a"), registration("z"))
    graph = definition(raw, pinned)
    calls: list[str] = []
    original = RegistryGeneration.resolve

    def observe(self: RegistryGeneration, binding: BindingDescriptor, **kwargs: Any) -> Any:
        calls.append(binding.binding_id)
        return original(self, binding, **kwargs)

    monkeypatch.setattr(RegistryGeneration, "resolve", observe)
    plan = compile_graph(graph, registry=pinned)
    assert calls == ["z", "a"]
    assert plan.index.preorder == ("r", "pre", "child", "post", "skill", "z", "a", "p")
    assert tuple(plan.bindings) == ("z", "a")
    assert [e.binding.binding_id for e in plan.manifest.binding_entries] == ["z", "a"]
    assert plan.bindings["z"].registration is pinned.lookup("z", "1")
    assert plan.index.get("/r/z", by="path") == graph.nodes["z"]
    assert plan.index.locations["p"].plane == "prompt"
    assert not plan.index.model_may_open("p")
    assert set(plan.index.locations) == set(graph.nodes)


def test_literal_templates_locale_process_and_explicit_generation_material() -> None:
    plan = compile_graph()
    assert plan.selected_locale == "zh-Hant-TW"
    assert plan.presentations["r"].title == "Localized"
    assert plan.presentations["r"].description == "Localized description"
    assert plan.presentations["z"].title == "z"
    assert compile_graph(locale="fr-CA").presentations["r"].title == "Default title"
    assert plan.processes["r"].pre_process == "pre"
    assert plan.processes["r"].post_process == "post"
    assert plan.processes["child"].pre_process is None  # variant is not designation/inheritance
    literal = plan.resolve_instructions("r", generation_uuid=GENERATION)
    assert isinstance(literal[0], TextPart)
    assert literal[0].text == "literal /r/t ctxg:v1:not-a-ref"
    parts = plan.resolve_instructions("skill", generation_uuid=GENERATION)
    assert isinstance(parts[0], TextPart) and parts[0].text == "raw /r/z <>&[]"
    refs = [part for part in parts if isinstance(part, ResolvedNodeRef)]
    assert [ref.label for ref in refs] == ["[label](unsafe)\n", "", None]
    assert [ref.reference.node_id for ref in refs] == ["z", "p", "a"]
    assert all(parse_reference(str(ref.reference)).generation_uuid == GENERATION for ref in refs)
    assert isinstance(plan.instructions["skill"][1], NodeRefPart)
    assert plan.instructions["skill"][1].target_id == "z"
    other = plan.resolve_instructions("skill", generation_uuid=OTHER_GENERATION)
    assert other[0] == parts[0] and other[1] != parts[1]
    for node_id in ("r", "skill"):
        with pytest.raises(GraphError):
            plan.resolve_instructions(node_id, generation_uuid="not-a-uuid")
    with pytest.raises(GraphError, match="no instruction"):
        plan.resolve_instructions("z", generation_uuid=GENERATION)
    with pytest.raises(GraphError):
        plan.resolve_instructions("missing", generation_uuid=GENERATION)
    with pytest.raises(TypeError):
        plan.resolve_instructions("r")


def resource() -> dict[str, Any]:
    return dict(kind="resource", publication_id="resource", name="data", uri="test:data",
                mime_type="application/json", opens_id="z")


def test_publication_evidence_is_fresh_exact_and_registry_owned() -> None:
    raw = raw_graph()
    raw["publications"] = [resource(), dict(kind="route", publication_id="route", method="GET",
        path="/data", opens_id="a"), dict(kind="prompt", publication_id="prompt", name="reserved",
        description="Prompt", opens_id="r", model_may_open=False)]
    graph = definition(raw)
    plan = compile_graph(graph)
    assert plan.index.publications["z"] == ("resource",)
    assert plan.publication_contracts["z"] == plan.bindings["z"].publication_contract
    assert not plan.index.model_may_open("r")
    for changed in (registration(effect="write"), registration(argument_free=False)):
        with pytest.raises(GraphError, match="argument-free read-only"):
            compile_graph(graph, registry=registry(changed))
    # An author's old accepted Resource evidence cannot bypass this compiler's index.
    graph = definition(raw | {"publications": raw["publications"][1:2]})
    with pytest.raises(GraphError, match="effect mismatch"):
        compile_graph(graph, registry=registry(registration(effect="write")))
    post = raw_graph()
    post["publications"] = [dict(kind="route", publication_id="write", method="POST",
                                 path="/write", opens_id="z")]
    writes = registry(registration(effect="write", argument_free=False))
    graph = definition(post, writes)
    assert compile_graph(graph, registry=writes).manifest.binding_entries[0].effect == "write"
    with pytest.raises(GraphError, match="effect mismatch"):
        compile_graph(graph)


class ExplosiveRegistry:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError("Disclosure compilation touched registry: " + name)


def disclosure_graph() -> GraphDefinition:
    raw = raw_graph()
    raw["mode"] = "disclosure_only"
    for node in raw["nodes"]:
        node.pop("binding", None)
        node.pop("input_contract", None)
    return GraphDefinition.from_json(raw)


def test_disclosure_never_touches_even_explosive_registry_or_tool_schema(monkeypatch: Any) -> None:
    graph = disclosure_graph()
    import contexture.core.graph.bindings as bindings

    monkeypatch.setattr(RegistryGeneration, "resolve", forbidden)
    monkeypatch.setattr(RegistryGeneration, "lookup", forbidden)
    monkeypatch.setattr(bindings, "_schema", forbidden)
    plan = compile_graph(graph, registry=ExplosiveRegistry(), no_execution_registry_digest=NO_EXECUTION)
    assert plan.bindings == {} and plan.publication_contracts == {}
    assert plan.manifest.binding_entries == ()
    assert plan.manifest.registry_digest == NO_EXECUTION
    assert plan.index.get("z").binding is None and plan.index.get("z").input_contract is None
    assert "z" not in plan.instructions
    assert plan.processes["r"].pre_process == "pre"
    with pytest.raises(GraphError, match="Explicit no-execution"):
        compile_graph(graph, registry=ExplosiveRegistry())
    with pytest.raises(GraphError):
        compile_graph(graph, registry=ExplosiveRegistry(), no_execution_registry_digest="none")
    monkeypatch.undo()
    with pytest.raises(GraphError, match="No-execution identity"):
        compile_graph(no_execution_registry_digest=NO_EXECUTION)
    # Even runtime with zero tools pins an actual registry, never a fake sentinel.
    raw = raw_graph()
    raw["nodes"] = [role("root")]
    raw["roots"] = [{"node_id": "root", "plane": "model"}]
    raw["presentations"]["catalogs"] = []
    with pytest.raises(GraphError, match="Pinned registry"):
        compile_graph(definition(raw), registry=None)


def test_pure_no_handlers_decoders_validators_factories_io_or_resource_prepare(monkeypatch: Any) -> None:
    import builtins
    import socket

    calls = {name: 0 for name in ("handler", "decoder", "validator", "factory")}

    def counted(name: str) -> Any:
        def call(*args: object, **kwargs: object) -> object:
            calls[name] += 1
            raise AssertionError(name)
        return call

    pinned = registry(registration(**{name: counted(name) for name in calls}))
    graph = definition()
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    plan = compile_graph(graph, registry=pinned)
    plan.resolve_instructions("skill", generation_uuid=GENERATION)
    assert calls == dict.fromkeys(calls, 0)
    assert not hasattr(plan, "invoke") and not hasattr(plan, "admit")


def test_author_source_mutation_and_detached_wire_cannot_change_deep_plan() -> None:
    raw = raw_graph()
    raw["extensions"] = {"vendor:copy": {"items": ["old"]}}
    graph = definition(raw)
    environments = ["python-3.14"]
    plan = compile_graph(graph, environment_compatibility=environments)
    before = plan.definition.as_json(), plan.manifest.as_json()
    raw["nodes"].clear()
    raw["roots"].clear()
    raw["presentations"]["catalogs"].clear()
    raw["extensions"]["vendor:copy"]["items"].append("evil")
    environments.append("evil")
    wire = plan.manifest.as_json()
    wire["binding_entries"][0]["binding"]["config"]["evil"] = 1
    assert (plan.definition.as_json(), plan.manifest.as_json()) == before
    for mapping in (plan.bindings, plan.publication_contracts, plan.presentations,
                    plan.instructions, plan.processes, plan.index.locations, plan.definition.nodes):
        with pytest.raises(TypeError):
            mapping["evil"] = None
    with pytest.raises(TypeError):
        plan.definition.extensions["vendor:copy"]["items"][0] = "evil"
    with pytest.raises(TypeError):
        plan.bindings["z"].entry.input_schema["evil"] = True
    with pytest.raises(FrozenInstanceError):
        plan.selected_locale = "en"
    assert plan.definition.extensions["vendor:copy"]["items"] == ("old",)


@pytest.mark.parametrize("changes", [
    {"compiler_contract_version": ""}, {"compiler_build": ""},
    {"compiler_build": "bad\ud800"}, {"app_dependency_contract": None},
    {"app_dependency_contract": {"dependency_contract_id": "d", "build": "b"}},
    {"environment_compatibility": ("same", "same")},
    {"environment_compatibility": "python"}, {"environment_compatibility": ("",)},
    {"locale": "bad_tag"}, {"registry": None},
    {"execution_extensions": ("vendor:execute",)}, {"execution_extensions": ""},
])
def test_invalid_explicit_contracts_refuse(changes: dict[str, Any]) -> None:
    with pytest.raises(GraphError):
        compile_graph(**changes)


@pytest.mark.parametrize("change", [
    {"version": "latest"}, {"kind": "remote"}, {"config": {"limit": 0}},
    {"config": {"unknown": 1}}, {"secret_refs": ["unknown"]},
    {"expected_contract_digest": "sha256:" + "0" * 64},
])
def test_exact_binding_errors_before_execution(change: dict[str, Any]) -> None:
    raw = raw_graph()
    for node in raw["nodes"]:
        if node["node_id"] == "z":
            node["binding"].update(change)
    # No executable publications, so decode does not resolve binding evidence.
    graph = GraphDefinition.from_json(raw)
    with pytest.raises(GraphError):
        compile_graph(graph)


def test_input_assertion_never_overrides_registered_schema() -> None:
    raw = raw_graph()
    raw["nodes"][0]["input_contract"] = True
    with pytest.raises(GraphError, match="assertion mismatch"):
        compile_graph(GraphDefinition.from_json(raw))


def test_all_tools_including_prompt_plane_resolve_and_false_assertion_is_not_absent() -> None:
    raw = raw_graph()
    raw["nodes"].append(tool("prompt-tool", "denied"))
    next(n for n in raw["nodes"] if n["node_id"] == "p")["members"] = {"tools": ["prompt-tool"]}
    raw["nodes"][-1]["input_contract"] = False
    pinned = registry(registration(), registration("denied", input_schema=False, argument_free=False))
    graph = GraphDefinition.from_json(raw)
    plan = compile_graph(graph, registry=pinned)
    assert tuple(plan.bindings) == ("z", "a", "prompt-tool")
    assert plan.bindings["prompt-tool"].entry.input_schema is False
    assert plan.index.locations["prompt-tool"].plane == "prompt"
    assert not plan.index.model_may_open("prompt-tool")
    with pytest.raises(GraphError, match="exact binding"):
        compile_graph(graph)
    with pytest.raises(GraphError, match="assertion mismatch"):
        compile_graph(graph, registry=registry(registration(), registration("denied")))


def test_disclosure_rejects_executable_publications_and_contracts_at_model_boundary() -> None:
    for publication in (resource(), dict(kind="route", publication_id="route", method="GET",
                                         path="/data", opens_id="z")):
        raw = disclosure_graph().as_json()
        raw["publications"] = [publication]
        with pytest.raises(GraphError, match="runtime Tool"):
            GraphDefinition.from_json(raw)
    raw = disclosure_graph().as_json()
    nodes = raw["nodes"]
    assert isinstance(nodes, list)
    next(n for n in nodes if n["node_id"] == "z")["input_contract"] = True
    with pytest.raises(GraphError, match="Disclosure-only"):
        GraphDefinition.from_json(raw)


def test_native_remote_pinning_and_deployment_contract_validation() -> None:
    portability = {"kind": "native", "language": "python", "version": "3.14", "factory": "app.make"}
    raw = raw_graph()
    for node in raw["nodes"]:
        if node["kind"] == "tool":
            node["binding"].update(kind="remote", portability=portability)
    pinned = registry(registration(kind="remote", portability=Portability.from_json(portability)))
    plan = compile_graph(GraphDefinition.from_json(raw), registry=pinned)
    assert plan.bindings["z"].entry.binding.portability.kind == "native"
    assert plan.bindings["z"].entry.binding.kind == "remote"
    for values in (("", "build"), ("deps", "")):
        with pytest.raises(GraphError):
            AppDependencyContract(*values)


def test_inert_namespace_metadata_allowed_declared_execution_unsupported() -> None:
    raw = raw_graph()
    raw["extensions"] = {"vendor:metadata": {"execution": "inert text"}}
    raw["nodes"][0]["extensions"] = {"vendor:copy": ["not executable"]}
    plan = compile_graph(definition(raw))
    assert plan.definition.extensions["vendor:metadata"]["execution"] == "inert text"
    with pytest.raises(GraphError, match="Executable extensions"):
        compile_graph(plan.definition, execution_extensions=("vendor:metadata",))
    with pytest.raises(GraphError, match="Executable extensions"):
        compile_graph(disclosure_graph(), no_execution_registry_digest=NO_EXECUTION,
                      execution_extensions=("unknown:semantics",))


def test_manifest_identity_schema_determinism_and_no_generation_in_manifest() -> None:
    plan = compile_graph()
    other = compile_graph()
    assert plan == other
    assert plan.index is not other.index
    assert len(plan.manifest.binding_entries) == 2  # Do not deduplicate repeated binding.
    assert plan.manifest.binding_entries[0] == plan.manifest.binding_entries[1]
    schema = json.loads((Path(__file__).parents[1] / "spec/managed/schemas/managed.schema.json").read_text())
    Draft202012Validator({"$ref": "#/$defs/ExecutionManifest", "$defs": schema["$defs"]}).validate(
        plan.manifest.as_json())
    assert ExecutionManifest.from_json(plan.manifest.as_json()) == plan.manifest
    variants = [compile_graph(registry=registry(build="other")),
        compile_graph(registry=registry(registration(decoder_contract_id="decoder-v2"))),
        compile_graph(registry=registry(registration(artifact_digest="sha256:" + "b" * 64))),
        compile_graph(compiler_build="other"), compile_graph(compiler_contract_version="other"),
        compile_graph(app_dependency_contract=AppDependencyContract("deps-v2", "build-1")),
        compile_graph(app_dependency_contract=AppDependencyContract("deps-v1", "build-2")),
        compile_graph(environment_compatibility=("python-3.11",))]
    assert all(v.definition_digest == plan.definition_digest for v in variants)
    assert len({plan.manifest.manifest_digest, *(v.manifest.manifest_digest for v in variants)}) == 9
    raw = raw_graph()
    next(n for n in raw["nodes"] if n["node_id"] == "z")["binding"]["config"] = {"limit": 1}
    assert compile_graph(definition(raw)).manifest.manifest_digest != plan.manifest.manifest_digest
    raw = raw_graph()
    raw["nodes"].reverse()
    assert compile_graph(definition(raw)) == plan
    digest = plan.manifest.manifest_digest
    plan.resolve_instructions("skill", generation_uuid=GENERATION)
    plan.resolve_instructions("skill", generation_uuid=OTHER_GENERATION)
    assert plan.manifest.manifest_digest == digest
    assert compile_graph(locale="en").manifest == plan.manifest


def test_real_index_rechecks_complete_candidate_not_author_evidence(monkeypatch: Any) -> None:
    from contexture.core.graph.index import GraphIndex

    original = GraphIndex.build.__func__
    calls: list[object] = []

    def observed(cls: type[GraphIndex], value: GraphDefinition, **kwargs: Any) -> GraphIndex:
        calls.append(kwargs.get("publication_contracts"))
        return original(cls, value, **kwargs)

    graph = definition()
    monkeypatch.setattr(GraphIndex, "build", classmethod(observed))
    plan = compile_graph(graph)
    assert calls == [plan.publication_contracts]
    assert plan.index.definition is graph


def test_module_has_no_io_legacy_static_or_business_dependencies() -> None:
    path = Path(__file__).parents[1] / "contexture/core/graph/compiler.py"
    tree = ast.parse(path.read_text())
    imports = [name for node in ast.walk(tree) for name in (
        [alias.name for alias in node.names] if isinstance(node, ast.Import)
        else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])]
    allowed = {"__future__", "collections.abc", "dataclasses", "types", "bindings", "errors",
               "index", "model", "references"}
    assert set(imports) <= allowed