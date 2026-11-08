"""Bounded WP03/WP07 static lowering; internal API, not a wire protocol.

Consumes an existing registered Index exactly once. No factories, discovery,
compilation of author methods, preparation, or invocation occurs here. The caller
owns trusted registry handlers/services; this module never infers a registration
from an opaque closure or retains an author object in its result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from inspect import getattr_static
from typing import Literal

from ..core.graph.bindings import AppDependencyContract, RegistryGeneration
from ..core.graph.canonical import validate_id
from ..core.graph.errors import GraphError
from ..core.graph.model import BindingDescriptor, GraphDefinition, InstructionTemplate
from ..core.graph.model import PublicationContract, TextPart, normalize_locale
from ..core.mcp_interface import Prompt, Resource
from ..core.model.binding import PlainBinding
from ..core.model.index import Index
from ..core.model.node import ContextNode
from ..core.model.role import PostProcess, PreProcess, Role
from ..core.model.skill import Skill
from ..core.model.tool import Tool
from ..core.presentation import Presentation, PresentationCatalog


def _fail(message: str) -> GraphError:
    return GraphError("static_lowering_invalid", message)


@dataclass(frozen=True, slots=True)
class DeploymentPresentation:
    """Exact deployment copy; None locale denotes a language-neutral source."""

    locale: str | None
    title: str | None
    description: str | None
    gateway: tuple[tuple[str, str], ...] | None


@dataclass(frozen=True, slots=True)
class PublicationMetadata:
    publication_id: str
    declared_name: str | None
    description: str
    declared_mime_type: str | None


@dataclass(frozen=True, slots=True)
class DeploymentMetadata:
    presentations: tuple[DeploymentPresentation, ...]
    publications: tuple[PublicationMetadata, ...]
    app_dependency_contract: AppDependencyContract | None


@dataclass(frozen=True, slots=True)
class LiteralInventory:
    """Unreviewed text, NOT a regex count or a claim that no refs remain.

    Even explicit templates can contain unresolved references in Text parts.
    Original author text is retained for review when a template replaces it.
    """

    node_id: str
    original_text: str
    template_texts: tuple[str, ...] | None
    status: Literal["unreviewed"] = "unreviewed"


@dataclass(frozen=True, slots=True)
class LoweredStaticGraph:
    definition: GraphDefinition
    deployment: DeploymentMetadata
    literal_inventory: tuple[LiteralInventory, ...]
    registry_digest: str | None


def _canonical_ref(ref: str, known: Mapping[str, object]) -> str:
    if (type(ref) is not str or not ref or any(not part for part in ref.split("/"))
            or ref not in known):
        raise _fail("Expected an exact canonical registered author ref; aliases are forbidden")
    return ref


def _standard_node(node: ContextNode) -> None:
    """Allow constructor/invoke subclasses, not altered disclosure semantics.

    Never call an override to decide whether it is safe. Trusted lower adapters
    for changed disclosure semantics are a separate, not implemented contract.
    """
    base = Role if isinstance(node, Role) else Skill if isinstance(node, Skill) else Tool
    if not isinstance(node, (Role, Skill, Tool)):
        raise _fail("Unknown node kind needs a trusted lower adapter")
    for method in ("compile", "card", "routing_card", "_compile_route", "_compile_inspect",
                   "_compile_active", "members", "branches", "__getattribute__"):
        if getattr_static(node, method) is not getattr_static(base, method):
            raise _fail("Custom disclosure/compile semantics need a trusted lower adapter")
    fields = ["name", "description", "uses", "path", "channels", "kind", "group"]
    if isinstance(node, (Role, Skill)):
        fields.append("instructions")
    if isinstance(node, Role):
        fields.extend(("pre_process", "children", "post_process", "skills", "tools"))
    if isinstance(node, Tool):
        fields.append("read_only")
    for name in fields:
        if getattr_static(type(node), name) is not getattr_static(base, name):
            raise _fail("Custom structural field semantics need a trusted lower adapter")


def _members(node: Role) -> tuple[dict[str, object], tuple[ContextNode, ...]]:
    slots: dict[str, object] = {}
    ordered: list[ContextNode] = []
    for name in ("pre_process", "children", "post_process", "skills", "tools"):
        value = getattr(node, name)
        singleton = name in ("pre_process", "post_process")
        values = (() if value is None else (value,)) if singleton else tuple(value)
        expected = {"pre_process": PreProcess, "children": Role, "post_process": PostProcess,
                    "skills": Skill, "tools": Tool}[name]
        if any(not isinstance(member, expected) for member in values):
            raise _fail("Member does not match its static typed slot")
        slots[name] = values
        ordered.extend(values)
    return slots, tuple(ordered)


def lower_static_index(
    index: Index, *, graph_id: str, application_name: str,
    mode: Literal["runtime", "disclosure_only"], node_ids: Mapping[str, str],
    root_planes: Mapping[str, Literal["model", "prompt"]],
    default_locale: str,
    presentation: Presentation | PresentationCatalog = Presentation(),
    publications: Mapping[str, Prompt | Resource] | None = None,
    resource_mime_types: Mapping[str, str] | None = None,
    mutability: Mapping[str, Literal["inherit"] | bool] | None = None,
    instruction_templates: Mapping[str, InstructionTemplate] | None = None,
    node_extensions: Mapping[str, Mapping[str, object]] | None = None,
    bindings: Mapping[str, BindingDescriptor] | None = None,
    registry: RegistryGeneration | None = None,
    assert_index_schemas: bool = False,
    app_dependency_contract: AppDependencyContract | None = None,
) -> LoweredStaticGraph:
    """Lower only explicitly supplied static declarations without side effects.

    node_ids/root_planes use exact registered refs (no leading/trailing slash).
    bindings, mutability and templates are keyed by supplied stable node IDs.
    An explicit authored creation policy cannot be overridden by mutability.
    Publications map explicit publication IDs to already constructed declarations;
    factories are not accepted. Missing Resource MIME must be explicitly supplied
    by publication ID: no transport default is guessed. Resource descriptions and
    declared optional fields remain in frozen deployment metadata.
    node_extensions explicitly supplies inert namespaced JSON by stable node ID.
    No contributor, serializer, author method or execution adapter is inferred.

    Runtime requires a bound Index plus a trusted pinned registry and an exact
    Tool-ID -> descriptor map, even with PlainBinding. Optional schema comparison
    refuses PlainBinding (its {} is not signature evidence). Registry attestations
    own decoder/default/validator semantics; comparison is not proof of callable
    equivalence. Channels are not captured: explicit dependency identity is needed
    when the source has Channels. No Channels are opened or serialized.

    Disclosure requires an unbound Index and no Channels/Resources/bindings.
    Its registry argument is completely ignored, without lookup/schema resolution.
    No Route input exists: the compiled application has no Route declarations,
    and the current Route.status has no lossless managed publication field.
    """
    if type(index) is not Index:
        raise _fail("Expected an existing registered Index")
    if mode not in ("runtime", "disclosure_only"):
        raise _fail("Explicit graph mode is required")
    if type(assert_index_schemas) is not bool:
        raise _fail("Schema assertion flag must be boolean")
    if index.is_bound != (mode == "runtime"):
        raise _fail("Graph mode must match bound/unbound Index")
    declarations = dict(publications or {})
    descriptors = dict(bindings or {})
    if mode == "disclosure_only":
        if index.channels is not None or descriptors or any(
                isinstance(p, Resource) for p in declarations.values()):
            raise _fail("Disclosure-only lowering rejects Channels, bindings and Resources")
        if assert_index_schemas:
            raise _fail("Disclosure-only lowering cannot assert execution schemas")
    elif type(registry) is not RegistryGeneration:
        raise _fail("Runtime lowering requires trusted pinned RegistryGeneration proof")
    if (app_dependency_contract is not None
            and type(app_dependency_contract) is not AppDependencyContract):
        raise _fail("Expected a validated deployment dependency contract")
    if index.channels is not None and app_dependency_contract is None:
        raise _fail("Channels require explicit deployment dependency identity")

    # Index.walk is a single registration inventory, never author compile().
    entries = tuple(index.walk())
    known = dict(entries)
    identities = dict(node_ids)
    if set(identities) != set(known):
        raise _fail("Stable ID map must exactly cover registered canonical author refs")
    for ref, identity in identities.items():
        _canonical_ref(ref, known)
        validate_id(identity)
    if len(set(identities.values())) != len(identities):
        raise _fail("Stable node IDs must be unique")
    by_object = {id(node): ref for ref, node in entries}
    if len(by_object) != len(entries):
        raise _fail("Registered node alias is forbidden")

    def identity_of(node: ContextNode) -> str:
        ref = by_object.get(id(node))
        if ref is None:
            raise _fail("Author containment changed after registration")
        return identities[ref]

    roots = tuple(index.roots)
    root_refs = tuple(by_object.get(id(root), "") for root in roots)
    planes = dict(root_planes)
    if set(planes) != set(root_refs):
        raise _fail("Root planes must exactly cover registered roots")
    for ref in planes:
        _canonical_ref(ref, known)
    mutable = dict(mutability or {})
    templates = dict(instruction_templates or {})
    if node_extensions is not None and not isinstance(node_extensions, Mapping):
        raise _fail("Node extensions must be an explicit stable-ID mapping")
    extensions = dict(node_extensions) if node_extensions is not None else {}
    ids = set(identities.values())
    if set(mutable) - ids or set(templates) - ids:
        raise _fail("Unknown stable ID in mutability or instruction template map")
    if set(extensions) - ids:
        raise _fail("Unknown stable ID in node extensions map")
    tool_ids = {identities[ref] for ref, node in entries if isinstance(node, Tool)}
    if mode == "runtime" and set(descriptors) != tool_ids:
        raise _fail("Every runtime Tool needs explicit descriptor and pinned schema/validator evidence")

    # Check that the Index and current author declarations still describe the
    # same forest. No restamping/reindexing and no post-lowering author queries.
    traversal: list[ContextNode] = []
    pending: list[tuple[ContextNode, ContextNode | None, str]] = [
        (root, None, root_refs[i]) for i, root in reversed(tuple(enumerate(roots)))]
    seen: set[int] = set()
    raw_nodes: list[dict[str, object]] = []
    contracts: dict[str, PublicationContract] = {}
    inventory: list[LiteralInventory] = []
    while pending:
        node, parent, ref = pending.pop()
        if id(node) in seen or by_object.get(id(node)) != ref:
            raise _fail("Containment cycle, alias or registration drift")
        seen.add(id(node))
        _standard_node(node)
        if node.channels is not index.channels:
            raise _fail("Node Channels differ from the registered deployment handle")
        if node.path != tuple(ref.split("/")) or node.name != ref.split("/")[-1]:
            raise _fail("Author path/name changed after registration")
        if index.parent_of(node) is not parent:
            raise _fail("Author parent changed after registration")
        traversal.append(node)
        node_id = identities[ref]
        expected_kind = "role" if isinstance(node, Role) else "skill" if isinstance(node, Skill) else "tool"
        if node.kind != expected_kind:
            raise _fail("Custom node kind needs a trusted lower adapter")
        authored_policy = node.mutable
        creation_policy = mutable.get(node_id, authored_policy)
        if type(authored_policy) is not bool and (
            type(authored_policy) is not str or authored_policy != "inherit"
        ):
            raise _fail("Invalid authored creation policy")
        if authored_policy != "inherit" and creation_policy != authored_policy:
            raise _fail("Mutability map conflicts with authored creation policy")
        raw: dict[str, object] = {"node_id": node_id, "kind": expected_kind,
            "name": node.name, "description": node.description,
            "mutable": creation_policy,
            "extensions": extensions.get(node_id, {}),
            "uses": [identities[_canonical_ref(target, known)] for target in node.uses]}
        if isinstance(node, (Role, Skill)):
            template = templates.get(node_id)
            if template is None:
                raw["instructions"] = node.instructions
                texts = None
            else:
                if type(template) is not InstructionTemplate:
                    raise _fail("Expected validated typed instruction template")
                raw["instruction_template"] = template.as_json()
                texts = tuple(p.text for p in template.parts if isinstance(p, TextPart))
            inventory.append(LiteralInventory(node_id, node.instructions, texts))
        elif node_id in templates:
            raise _fail("Tools cannot have instruction templates")
        if isinstance(node, Role):
            slots, members = _members(node)
            raw_members: dict[str, object] = {}
            for slot, members_in_slot in slots.items():
                # _members produces tuples of constructed ContextNodes only.
                assert isinstance(members_in_slot, tuple)
                lowered = [identity_of(member) for member in members_in_slot]
                if slot in ("pre_process", "post_process"):
                    if lowered:
                        raw_members[slot] = lowered[0]
                else:
                    raw_members[slot] = lowered
            raw["members"] = raw_members
            raw["role_variant"] = ("pre_process" if isinstance(node, PreProcess) else
                                   "post_process" if isinstance(node, PostProcess) else "ordinary")
            pending.extend((child, node, ref + "/" + child.name) for child in reversed(members))
        if isinstance(node, Tool) and mode == "runtime":
            assert isinstance(registry, RegistryGeneration)
            if type(node.read_only) is not bool:
                raise _fail("Tool.read_only must be boolean")
            descriptor = descriptors[node_id]
            if type(descriptor) is not BindingDescriptor:
                raise _fail("Expected validated explicit BindingDescriptor")
            assertion: object = None
            if assert_index_schemas:
                binding = index.binding_of(ref)
                if isinstance(binding, PlainBinding):
                    raise _fail("PlainBinding {} is not actual signature evidence; supply trusted registry contracts")
                assertion = binding.schema
                if assertion is None:
                    raise _fail("Existing binding has no meaningful schema evidence")
            resolved = registry.resolve(descriptor, input_contract=assertion,
                expected_effect="read_only" if node.read_only else "write")
            raw["binding"] = descriptor.as_json()
            if assert_index_schemas:
                raw["input_contract"] = assertion
            contracts[node_id] = resolved.publication_contract
        raw_nodes.append(raw)
    if tuple(id(node) for node in traversal) != tuple(id(node) for _, node in entries):
        raise _fail("Author forest/order differs from registered Index")

    raw_presentations, deployment_presentations = _presentations(
        presentation, default_locale, identities)
    raw_publications, metadata = _publications(declarations, dict(resource_mime_types or {}), identities)
    definition = GraphDefinition.from_json({
        "schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": graph_id,
        "application_name": application_name, "mode": mode, "nodes": raw_nodes,
        "roots": [{"node_id": identities[ref], "plane": planes[ref]} for ref in root_refs],
        "publications": raw_publications, "presentations": raw_presentations,
    }, publication_contracts=contracts)
    return LoweredStaticGraph(definition,
        DeploymentMetadata(deployment_presentations, metadata, app_dependency_contract),
        tuple(inventory), registry.registry_digest if mode == "runtime" and registry is not None else None)


def _presentations(
    source: Presentation | PresentationCatalog, default_locale: str, identities: Mapping[str, str],
) -> tuple[dict[str, object], tuple[DeploymentPresentation, ...]]:
    default = normalize_locale(default_locale)
    if type(source) is PresentationCatalog:
        if default != source.default_locale:
            raise _fail("Explicit default locale differs from static catalog")
        locales = tuple(source.locales.items())
        neutral = False
    elif type(source) is Presentation:
        locales = ((default, source),)
        neutral = True
    else:
        raise _fail("Expected standard static presentation values")
    catalogs: list[dict[str, object]] = []
    deployment: list[DeploymentPresentation] = []
    for locale, value in locales:
        if type(value) is not Presentation:
            raise _fail("Custom presentation semantics need a trusted lower adapter")
        entries: list[dict[str, object]] = []
        for ref, copy in value.nodes.items():
            entry: dict[str, object] = {"node_id": identities[_canonical_ref(ref, identities)],
                                        "title": copy.title}
            if copy.description is not None:
                entry["description"] = copy.description
            entries.append(entry)
        catalogs.append({"locale": locale, "entries": entries})
        gateway = value.gateway
        deployment.append(DeploymentPresentation(None if neutral else locale, value.title,
            value.description, None if gateway is None else tuple(
                (name, getattr(gateway, name)) for name in
                ("discover", "inspect", "open", "invoke_read_only", "invoke"))))
    return {"default_locale": default, "catalogs": catalogs}, tuple(deployment)


def _publications(
    declarations: Mapping[str, Prompt | Resource], mime_types: Mapping[str, str],
    identities: Mapping[str, str],
) -> tuple[list[dict[str, object]], tuple[PublicationMetadata, ...]]:
    result: list[dict[str, object]] = []
    metadata: list[PublicationMetadata] = []
    resource_ids = {key for key, entry in declarations.items() if isinstance(entry, Resource)}
    if set(mime_types) - resource_ids:
        raise _fail("MIME evidence names an unknown Resource publication")
    for publication_id, entry in declarations.items():
        validate_id(publication_id)
        base = Prompt if isinstance(entry, Prompt) else Resource
        if not isinstance(entry, (Prompt, Resource)):
            raise _fail("Publications must be constructed Prompt/Resource declarations")
        names = ["opens", "name", "description", "kind"]
        names.extend(("model_may_open",) if isinstance(entry, Prompt) else ("uri", "mime_type"))
        if getattr_static(type(entry), "__getattribute__") is not object.__getattribute__:
            raise _fail("Custom publication access needs a trusted lower adapter")
        for name in names:
            if getattr_static(type(entry), name) is not getattr_static(base, name):
                raise _fail("Custom publication fields need a trusted lower adapter")
        target = _canonical_ref(entry.opens, identities)
        raw: dict[str, object] = {"kind": entry.kind, "publication_id": publication_id,
            "opens_id": identities[target], "name": entry.name if entry.name is not None
            else target.rsplit("/", 1)[-1]}
        if isinstance(entry, Prompt):
            raw.update(description=entry.description, model_may_open=entry.model_may_open)
            mime = None
        else:
            mime = entry.mime_type
            effective_mime = mime_types.get(publication_id, mime)
            if effective_mime is None:
                raise _fail("Resource requires explicit MIME evidence; transport defaults are not inferred")
            if mime is not None and effective_mime != mime:
                raise _fail("Explicit Resource MIME assertion mismatch")
            raw.update(uri=entry.uri, mime_type=effective_mime)
        result.append(raw)
        metadata.append(PublicationMetadata(publication_id, entry.name, entry.description, mime))
    return result, tuple(metadata)