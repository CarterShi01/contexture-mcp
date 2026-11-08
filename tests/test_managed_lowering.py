"""Static lowering facts, explicit evidence and forbidden-call regressions."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from contexture.application.managed_lowering import lower_static_index
from contexture.core.graph.bindings import AppDependencyContract, BindingRegistration, RegistryGeneration
from contexture.core.graph.compiler import GraphCompiler
from contexture.core.graph.canonical import thaw_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.index import GraphIndex
from contexture.core.graph.model import BindingDescriptor, InstructionTemplate, Portability
from contexture.core.mcp_interface import Prompt, Resource
from contexture.core.model import ControllerManager, PostProcess, PreProcess, Role, Skill, Tool
from contexture.core.model.index import Index
from contexture.core.presentation import GatewayPresentation, NodePresentation, Presentation, PresentationCatalog

CALLS: list[str] = []


def forbidden(*args: object, **kwargs: object) -> object:
    CALLS.append("forbidden")
    raise AssertionError("Lowering executed external code")


SCHEMA: dict[str, Any] = {"type": "object", "additionalProperties": False}
PORTABILITY = Portability.from_json({"kind": "native", "language": "python",
                                     "version": "1", "factory": "caller.owned"})


class Reader(Tool):
    def __init__(self) -> None:
        super().__init__(name="read", description="Read document", read_only=True)

    async def invoke(self) -> object:
        return forbidden()


class Workflow(Skill):
    def __init__(self) -> None:
        super().__init__(name="method", description="Method", instructions="  literal /ops/read\n",
                         uses=("ops/read",))


class Operator(Role):
    def __init__(self) -> None:
        super().__init__(name="ops", description="Operate", instructions="raw ops/method <&>\n",
            pre_process=PreProcess(name="prepare", description="Prepare", instructions="Before"),
            children=[PostProcess(name="branch", description="Branch", instructions="Branch")],
            post_process=PostProcess(name="finish", description="Finish", instructions="After"),
            skills=[Workflow()], tools=[Reader()])


class PublishedPrompt(Prompt):
    def __init__(self) -> None:
        super().__init__(opens="commands", description="Run command", model_may_open=False)


class PublishedDocument(Resource):
    def __init__(self) -> None:
        super().__init__(opens="ops/read", description="Document description", name="document",
                         uri="contexture://docs/runbook", mime_type="text/markdown")


def fixture(*, bound: bool = True, meaningful: bool = False) -> dict[str, Any]:
    manager = ControllerManager()
    role = manager.register_role(Operator)
    manager.register_skill(Skill(name="commands", description="Command", instructions="User"))
    # A legitimate uses cycle is not a containment traversal.
    role.tools[0].uses = ("ops/method",)

    class Binding:
        schema = {"type": "object", "additionalProperties": False}

        async def call(self, arguments: dict[str, Any] | None, context: Any) -> Any:
            return forbidden(arguments, context)

    index = (Index.of(manager, bind=lambda _: Binding()) if meaningful else Index.of(manager)) if bound else Index.unbound(manager)
    ids = {ref: "stable-" + str(i) for i, (ref, _) in enumerate(index.walk())}
    registration = BindingRegistration(binding_id="reader", version="1", kind="registered",
        portability=PORTABILITY, effect="read_only", abi="call-v1",
        artifact_digest="sha256:" + "a" * 64, input_schema=SCHEMA,
        validator_contract_id="validator-v1", decoder_contract_id="decoder-v1",
        default_contract_id="default-v1", coercion_contract_id="coercion-v1",
        handler=forbidden, validator=forbidden, decoder=forbidden, factory=forbidden,
        argument_free=True)
    registry = RegistryGeneration(build="pinned", registrations=[registration])
    descriptor = BindingDescriptor.from_json({"binding_id": "reader", "version": "1",
        "kind": "registered", "portability": PORTABILITY.as_json(),
        "expected_contract_digest": registration.contract_digest})
    return dict(index=index, graph_id="explicit-graph", application_name="application",
        mode="runtime" if bound else "disclosure_only", node_ids=ids,
        root_planes={"ops": "model", "commands": "prompt"}, default_locale="en",
        bindings={ids["ops/read"]: descriptor} if bound else {}, registry=registry)


def lower(options: dict[str, Any], **changes: Any) -> Any:
    values = options | changes
    index = values.pop("index")
    return lower_static_index(index, **values)


def test_lossless_structure_order_variants_cycles_and_compiler() -> None:
    options = fixture()
    ids = options["node_ids"]
    result = lower(options, publications={"prompt-id": PublishedPrompt(), "resource-id": PublishedDocument()},
                   mutability={ids["ops"]: True, ids["ops/branch"]: False})
    contracts = {ids["ops/read"]: options["registry"].resolve(
        options["bindings"][ids["ops/read"]]).publication_contract}
    index = GraphIndex.build(result.definition, publication_contracts=contracts)
    assert index.preorder == tuple(ids.values())
    assert [root.node_id for root in result.definition.roots] == [ids["ops"], ids["commands"]]
    assert result.definition.nodes[ids["ops/branch"]].role_variant == "post_process"
    assert result.definition.nodes[ids["ops/method"]].uses == (ids["ops/read"],)
    assert result.definition.nodes[ids["ops/read"]].uses == (ids["ops/method"],)
    members = result.definition.nodes[ids["ops"]].members
    assert members is not None
    assert [slot for slot, _, _ in members.placements()] == [
        "pre_process", "children", "post_process", "skills", "tools"]
    assert index.locations[ids["ops/method"]].effective_mutable
    assert not index.locations[ids["ops/branch"]].effective_mutable
    assert not index.model_may_open(ids["commands"])
    assert result.deployment.publications[1].description == "Document description"
    plan = GraphCompiler.compile(result.definition, registry=options["registry"],
        compiler_contract_version="compiler-v1", compiler_build="build",
        app_dependency_contract=AppDependencyContract("deps", "1"),
        environment_compatibility=(), locale="en")
    assert plan.processes[ids["ops"]].pre_process == ids["ops/prepare"]
    assert tuple(plan.bindings) == (ids["ops/read"],)
    assert plan.index.locations[ids["commands"]].plane == "prompt"
    assert not CALLS


def test_authored_creation_policy_is_captured_without_changing_static_cards() -> None:
    options = fixture(bound=False)
    nodes = dict(options["index"].walk())
    ids = options["node_ids"]
    before = nodes["ops"].compile("route")
    nodes["ops"].mutable = True
    nodes["ops/branch"].mutable = False
    nodes["ops/method"].mutable = "inherit"
    lowered = lower(options)
    index = GraphIndex.build(lowered.definition)
    assert nodes["ops"].compile("route") == before
    assert index.locations[ids["ops"]].effective_mutable
    assert index.locations[ids["ops/method"]].effective_mutable
    assert not index.locations[ids["ops/branch"]].effective_mutable
    nodes["ops"].mutable = False
    assert lowered.definition.nodes[ids["ops"]].mutable is True
    assert index.locations[ids["ops/method"]].effective_mutable


def test_authored_false_cannot_be_overridden_by_child_true_or_lowering_map() -> None:
    options = fixture(bound=False)
    nodes = dict(options["index"].walk())
    ids = options["node_ids"]
    nodes["ops"].mutable = False
    nodes["ops/branch"].mutable = True
    index = GraphIndex.build(lower(options).definition)
    assert not index.locations[ids["ops"]].effective_mutable
    assert not index.locations[ids["ops/branch"]].effective_mutable
    with pytest.raises(GraphError, match="conflicts with authored"):
        lower(options, mutability={ids["ops"]: True})
    assert lower(options, mutability={ids["ops"]: False}).definition.nodes[ids["ops"]].mutable is False


def test_all_locales_exact_copy_and_deployment_fields() -> None:
    options = fixture()
    source = PresentationCatalog(default_locale="en", locales={
        "en": Presentation(title="Product", description="Deployment", gateway=GatewayPresentation(open="Enter"),
            nodes={"ops": NodePresentation(title="Operations")}),
        "zh-Hant": Presentation(title="產品", nodes={"ops/read": NodePresentation(title="讀取", description="資料")})})
    result = lower(options, presentation=source)
    catalogs = result.definition.presentations.catalogs
    assert [catalog.locale for catalog in catalogs] == ["en", "zh-Hant"]
    assert catalogs[0].entries[0].description is None
    assert catalogs[1].entries[0].node_id == options["node_ids"]["ops/read"]
    assert catalogs[1].entries[0].description == "資料"
    assert result.deployment.presentations[0].title == "Product"
    assert result.deployment.presentations[0].description == "Deployment"
    assert dict(result.deployment.presentations[0].gateway or ())["open"] == "Enter"
    neutral = lower(options, presentation=Presentation(title="Neutral"))
    assert neutral.deployment.presentations[0].locale is None
    with pytest.raises(GraphError):
        lower(options, presentation=source, default_locale="fr")
    with pytest.raises(GraphError):
        lower(options, presentation=Presentation(nodes={"ops//read": NodePresentation(title="Alias")}))


def test_explicit_node_metadata_is_detached_inert_and_part_of_definition() -> None:
    options = fixture()
    identity = options["node_ids"]["ops"]
    metadata = {"host:catalog": {"source/path": {"items": [{"owner": "application"}]}}}
    before = options["index"].find("ops").compile("route")
    result = lower(options, node_extensions={identity: metadata})
    expected = {"host:catalog": {"source/path": {"items": [{"owner": "application"}]}}}
    assert thaw_json(result.definition.nodes[identity].extensions) == expected
    metadata["host:catalog"]["source/path"]["items"][0]["owner"] = "changed"
    assert thaw_json(result.definition.nodes[identity].extensions) == expected
    assert options["index"].find("ops").compile("route") == before
    assert lower(options).definition.nodes[identity].extensions == {}
    assert result.definition.definition_digest != lower(options).definition.definition_digest
    compiled = GraphCompiler.compile(result.definition, registry=options["registry"],
        compiler_contract_version="compiler-v1", compiler_build="build",
        app_dependency_contract=AppDependencyContract("deps", "1"),
        environment_compatibility=(), locale="en")
    assert thaw_json(compiled.definition.nodes[identity].extensions) == expected
    assert not CALLS


@pytest.mark.parametrize("metadata", [
    {"unknown-id": {"host:data": {}}},
    {"stable-0": {"not-namespaced": {}}},
    {"stable-0": {"host:data": object()}},
    {"stable-0": []},
    [],
])
def test_node_metadata_rejects_unknown_ids_invalid_namespaces_and_non_json(metadata: Any) -> None:
    with pytest.raises(GraphError):
        lower(fixture(bound=False), node_extensions=metadata)
    assert not CALLS


def test_literals_remain_literal_and_templates_are_explicit() -> None:
    options = fixture()
    ids = options["node_ids"]
    literal = lower(options)
    assert literal.definition.nodes[ids["ops"]].instructions == "raw ops/method <&>\n"
    assert all(item.status == "unreviewed" for item in literal.literal_inventory)
    template = InstructionTemplate.from_json({"parts": [
        {"kind": "Text", "text": "literal ops/read"},
        {"kind": "NodeRef", "target_id": ids["ops/read"], "label": ""}]})
    result = lower(options, instruction_templates={ids["ops"]: template})
    assert result.definition.nodes[ids["ops"]].instruction_template == template
    inventory = next(item for item in result.literal_inventory if item.node_id == ids["ops"])
    assert inventory.original_text == "raw ops/method <&>\n"
    assert inventory.template_texts == ("literal ops/read",)
    bad = InstructionTemplate.from_json({"parts": [{"kind": "NodeRef", "target_id": "missing"}]})
    with pytest.raises(GraphError):
        lower(options, instruction_templates={ids["ops"]: bad})


def test_detached_result_survives_author_and_input_mutation() -> None:
    options = fixture(meaningful=True)
    index = options["index"]
    prompt = PublishedPrompt()
    result = lower(options, publications={"publication": prompt}, assert_index_schemas=True)
    before = result.definition.as_json()
    role = index.find("ops")
    role.instructions = "changed"
    role.children.clear()
    index.find("ops/method").uses = ()
    index.find("ops/read").description = "changed"
    index.binding_of("ops/read").schema["properties"] = {"new": {"type": "string"}}
    prompt.description = "changed"
    options["node_ids"].clear()
    options["bindings"].clear()
    assert result.definition.as_json() == before
    assert result.deployment.publications[0].description == "Run command"
    with pytest.raises(FrozenInstanceError):
        result.deployment.publications[0].description = "changed"
    with pytest.raises(TypeError):
        result.definition.nodes["new"] = result.definition.nodes[next(iter(result.definition.nodes))]
    fresh = lower(fixture(meaningful=True), publications={"publication": PublishedPrompt()}, assert_index_schemas=True)
    assert fresh.definition.definition_digest == result.definition.definition_digest
    assert not CALLS


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "alias", "leading", "trailing"])
def test_stable_identity_mapping_must_be_complete_explicit_and_canonical(change: str) -> None:
    options = fixture()
    ids = dict(options["node_ids"])
    if change == "missing":
        ids.pop("ops/read")
    elif change == "extra":
        ids["unknown"] = "extra"
    elif change == "duplicate":
        ids["ops/read"] = ids["ops"]
    else:
        alias = {"alias": "ops//read", "leading": "/ops/read", "trailing": "ops/read/"}[change]
        ids[alias] = ids.pop("ops/read")
    with pytest.raises(GraphError):
        lower(options, node_ids=ids)


def test_schema_proof_plain_floor_mismatch_and_effect_fail_closed() -> None:
    options = fixture()
    with pytest.raises(GraphError, match="PlainBinding"):
        lower(options, assert_index_schemas=True)
    with pytest.raises(GraphError, match="pinned"):
        lower(options, registry=None)
    with pytest.raises(GraphError, match="Every runtime Tool"):
        lower(options, bindings={})
    assert lower(options).registry_digest == options["registry"].registry_digest
    options["index"].find("ops/read").read_only = False
    with pytest.raises(GraphError, match="effect mismatch"):
        lower(options)
    meaningful = fixture(meaningful=True)
    meaningful["index"].binding_of("ops/read").schema = {"type": "string"}
    with pytest.raises(GraphError, match="assertion mismatch"):
        lower(meaningful, assert_index_schemas=True)
    assert not CALLS


def test_disclosure_never_resolves_registry_or_schema(monkeypatch: Any) -> None:
    options = fixture(bound=False)
    monkeypatch.setattr(RegistryGeneration, "resolve", forbidden)
    monkeypatch.setattr(Index, "binding_of", forbidden)
    result = lower(options, registry=object(), publications={"prompt": PublishedPrompt()})
    assert result.registry_digest is None
    assert all(node.binding is None and node.input_contract is None for node in result.definition.nodes.values())
    with pytest.raises(GraphError, match="Resources"):
        lower(options, publications={"resource": PublishedDocument()})
    with pytest.raises(GraphError, match="cannot assert"):
        lower(options, assert_index_schemas=True)
    assert not CALLS


def test_channels_are_not_opened_or_captured_and_require_deployment_identity() -> None:
    options = fixture()

    class Channels:
        open = forbidden
        close = forbidden

    channels = Channels()
    # This emulates the already registered handle, not a lower-time provision.
    object.__setattr__(options["index"], "channels", channels)
    for _, node in options["index"].walk():
        node.channels = channels
    with pytest.raises(GraphError, match="dependency identity"):
        lower(options)
    dependency = AppDependencyContract("deployment-services", "build-1")
    result = lower(options, app_dependency_contract=dependency)
    assert result.deployment.app_dependency_contract == dependency
    assert "channels" not in result.definition.as_json()
    disclosure = fixture(bound=False)
    object.__setattr__(disclosure["index"], "channels", channels)
    with pytest.raises(GraphError, match="Channels"):
        lower(disclosure)
    assert not CALLS


@pytest.mark.parametrize("field", ["compile", "card", "_compile_active", "members", "branches"])
def test_custom_compile_semantics_refuse_without_executing_override(field: str) -> None:
    custom = type("Custom", (Operator,), {field: forbidden})
    # Register first with ordinary semantics; never execute custom members even
    # during fixture creation. A later class change is still detected fail closed.
    options = fixture()
    options["index"].find("ops").__class__ = custom
    with pytest.raises(GraphError, match="trusted lower adapter"):
        lower(options)
    assert not CALLS


@pytest.mark.parametrize("change", ["uses_alias", "list", "name", "path", "plane", "mutable"])
def test_registration_drift_aliases_and_invalid_maps_refuse(change: str) -> None:
    options = fixture()
    role = options["index"].find("ops")
    if change == "uses_alias":
        options["index"].find("ops/method").uses = ("ops//read",)
    elif change == "list":
        role.children.reverse()
        role.children.append(Role(name="added", description="Added", instructions="New"))
    elif change == "name":
        role.name = "renamed"
    elif change == "path":
        role.path = ("other",)
    elif change == "plane":
        options["root_planes"] = {"ops": "model"}
    else:
        options["mutability"] = {"unknown-id": True}
    with pytest.raises(GraphError):
        lower(options)


def test_resource_mime_and_pinned_argument_free_publication_evidence() -> None:
    options = fixture()
    document = PublishedDocument()
    document.mime_type = None
    with pytest.raises(GraphError, match="MIME evidence"):
        lower(options, publications={"doc": document})
    result = lower(options, publications={"doc": document}, resource_mime_types={"doc": "text/plain"})
    assert result.definition.publications[0].as_json()["mime_type"] == "text/plain"
    assert result.deployment.publications[0].declared_mime_type is None
    existing = options["registry"].lookup("reader", "1")
    from dataclasses import replace

    no_arg_proof = replace(existing, argument_free=False)
    registry = RegistryGeneration(build="other", registrations=[no_arg_proof])
    raw = options["bindings"][options["node_ids"]["ops/read"]].as_json()
    raw.pop("expected_contract_digest")
    with pytest.raises(GraphError, match="argument-free"):
        lower(options, registry=registry, bindings={options["node_ids"]["ops/read"]: BindingDescriptor.from_json(raw)},
              publications={"doc": PublishedDocument()})
    with pytest.raises(GraphError):
        lower(options, publications={"prompt": Prompt(opens="ops//method", description="Alias")})


def test_module_import_and_lowering_do_not_import_transport_sdk() -> None:
    import subprocess
    import sys

    code = """
import sys
from contexture.application.managed_lowering import lower_static_index
from contexture.core.model import Role
from contexture.core.model.index import Index
r = Role(name='r', description='Role', instructions='Text')
i = Index.unbound(r)
lower_static_index(i, graph_id='g', application_name='app', mode='disclosure_only',
    node_ids={'r': 'explicit'}, root_planes={'r': 'model'}, default_locale='en')
assert not any(k == 'mcp' or k.startswith('mcp.') for k in sys.modules)
assert not any(k == 'contexture.server' or k.startswith('contexture.server.') for k in sys.modules)
"""
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_real_demo_declarations_lower_without_blanket_subclass_rejection(monkeypatch: Any) -> None:
    from contexture.demo.role import KubernetesPlatform
    import builtins
    import socket

    index = Index.unbound(KubernetesPlatform)
    # These are caller supplied migration IDs, not generated by the lowerer.
    ids = {ref: f"fixture-seed-{i}" for i, (ref, _) in enumerate(index.walk())}
    expected = [(ref, node.name, node.description, tuple(node.uses)) for ref, node in index.walk()]
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = lower_static_index(index, graph_id="demo-graph", application_name="demo",
        mode="disclosure_only", node_ids=ids,
        root_planes={"kubernetes-platform": "model"}, default_locale="en")
    assert len(result.definition.nodes) == len(expected)
    for ref, name, description, uses in expected:
        node = result.definition.nodes[ids[ref]]
        assert (node.name, node.description, node.uses) == (name, description, tuple(ids[u] for u in uses))
    assert not CALLS


def test_mixed_root_kinds_process_roots_and_multi_member_order() -> None:
    pre = PreProcess(name="pre-root", description="Pre root", instructions="Pre")
    post = PostProcess(name="post-child", description="Post child", instructions="Post")
    role = Role(name="r", description="Role", instructions="Role", children=[post,
        PreProcess(name="pre-child", description="Pre", instructions="Pre")],
        skills=[Skill(name=n, description=n, instructions=n) for n in ("s2", "s1")],
        tools=[Tool(name=n, description=n) for n in ("t2", "t1")])
    manager = ControllerManager()
    manager.register_role(pre)
    manager.register_role(role)
    manager.register_skill(Skill(name="standalone", description="Skill", instructions="Skill"))
    manager.register_tool(Tool(name="standalone-tool", description="Tool"))
    index = Index.unbound(manager)
    ids = {ref: f"given-{i}" for i, (ref, _) in enumerate(index.walk())}
    result = lower_static_index(index, graph_id="g", application_name="app", mode="disclosure_only",
        node_ids=ids, root_planes={root.name: "model" for root in index.roots}, default_locale="en")
    assert [root.node_id for root in result.definition.roots] == [ids[root.name] for root in index.roots]
    assert result.definition.nodes[ids["pre-root"]].role_variant == "pre_process"
    slots = result.definition.nodes[ids["r"]].members
    assert slots is not None
    assert slots.skills == (ids["r/s2"], ids["r/s1"])
    assert slots.tools == (ids["r/t2"], ids["r/t1"])
    assert GraphIndex.build(result.definition).preorder == tuple(ids.values())


def test_explicit_remote_registry_not_schema_inferred_from_closure() -> None:
    from dataclasses import replace

    options = fixture()
    registered = options["registry"].lookup("reader", "1")
    remote = replace(registered, kind="remote", binding_id="explicit-remote")
    registry = RegistryGeneration(build="remote-pinned", registrations=[remote])
    descriptor = BindingDescriptor.from_json({"kind": "remote", "binding_id": remote.binding_id,
        "version": remote.version, "portability": remote.portability.as_json(),
        "expected_contract_digest": remote.contract_digest})
    result = lower(options, registry=registry, bindings={options["node_ids"]["ops/read"]: descriptor})
    tool = result.definition.nodes[options["node_ids"]["ops/read"]]
    assert tool.binding == descriptor and tool.input_contract is None
    assert not CALLS


def test_explicit_ids_not_path_identity_and_nested_process_designations() -> None:
    options = fixture(bound=False)
    role = options["index"].find("ops")
    role.pre_process.pre_process = PreProcess(name="nested", description="Nested", instructions="Nested")
    # Explicitly build a fresh registered Index; lowering must not repair or
    # stamp the earlier Index whose author containment has changed.
    index = Index.unbound([role, options["index"].find("commands")])
    supplied = {ref: f"opaque/身份/{i}" for i, (ref, _) in enumerate(index.walk())}
    paths = {ref: node.path for ref, node in index.walk()}
    result = lower(options, index=index, node_ids=supplied)
    graph = GraphIndex.build(result.definition)
    pre_id = supplied["ops/prepare"]
    assert result.definition.nodes[pre_id].members.pre_process == supplied["ops/prepare/nested"]
    assert graph.locations[supplied["ops/prepare/nested"]].slot == "pre_process"
    assert graph.locations[supplied["ops/branch"]].slot == "children"
    assert {ref: node.path for ref, node in index.walk()} == paths
    assert set(result.definition.nodes) == set(supplied.values())
    assert all(node.mutable == "inherit" for node in result.definition.nodes.values())


def test_pinned_schema_detached_and_optional_assertion_does_not_read_plain_schema(monkeypatch: Any) -> None:
    from contexture.core.model.binding import PlainBinding

    options = fixture()
    monkeypatch.setattr(PlainBinding, "schema", property(forbidden))
    result = lower(options)
    registration = options["registry"].lookup("reader", "1")
    with pytest.raises(TypeError):
        registration.input_schema["new"] = True
    assert result.registry_digest == options["registry"].registry_digest
    assert not CALLS