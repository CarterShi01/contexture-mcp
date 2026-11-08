"""Bounded WP07 pure compiler; not static lowering, preparation or a Host.

StructuralPlan and its typed instruction materials are LOCAL implementation APIs,
not an accepted Catalog/instructions wire boundary. In particular a resolved ref
is syntax, not an authorized returned view. WP08/09 must apply visibility, leases
and current admission before emitting it. No markdown/escaping convention is
invented here. Labels (including empty labels) remain separate human text.

Extensions in the current model are inert namespaced JSON. A caller declaring
execution semantics must supply execution_extensions; none are supported by this
compiler, so every such declaration refuses. No keyword guessing promotes inert
metadata into executable semantics. Extension negotiation itself remains future
work, not an alternate accepted protocol.

Disclosure-only callers must explicitly supply no_execution_registry_digest: an
identity attested by their deployment using the existing manifest Digest field.
There is no normative sentinel/empty-registry identity. This module neither
invents one nor touches a supplied registry in disclosure-only mode.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .bindings import (
    AppDependencyContract, ExecutionManifest, RegistryGeneration, ResolvedBinding,
)
from .errors import GraphError
from .index import GraphIndex
from .model import (
    GraphDefinition, InstructionTemplate, NodeRefPart, PublicationContract, TextPart, normalize_locale,
)
from .references import ManagedReference


@dataclass(frozen=True, slots=True)
class EffectivePresentation:
    """Human copy only; never replaces a node's structural name or instructions."""

    title: str
    description: str


@dataclass(frozen=True, slots=True)
class ProcessPlan:
    """Only dedicated owner slots designate processes; no automatic execution."""

    pre_process: str | None
    post_process: str | None


@dataclass(frozen=True, slots=True)
class ResolvedNodeRef:
    """Local typed syntax material, NOT an authorized disclosure/wire result.

    None means no author label; empty string means an explicitly empty label.
    Consumers must not substitute a raw author path or concatenate unescaped
    labels into a protocol. The canonical reference is kept in a separate field.
    """

    reference: ManagedReference
    label: str | None


@dataclass(frozen=True, slots=True)
class StructuralPlan:
    """Read-only snapshot for future preparation and disclosure consumers.

    All mappings are compiler-owned proxies; all values are frozen model/index/
    binding values. Trusted callable references retain the binding module's
    reentrancy caveat: this does not freeze external services or closure cells.
    No business invocation or admission API is provided.
    """

    index: GraphIndex
    bindings: Mapping[str, ResolvedBinding]
    publication_contracts: Mapping[str, PublicationContract]
    manifest: ExecutionManifest
    selected_locale: str
    presentations: Mapping[str, EffectivePresentation]
    instructions: Mapping[str, tuple[TextPart | NodeRefPart, ...]]
    processes: Mapping[str, ProcessPlan]

    @property
    def definition(self) -> GraphDefinition:
        return self.index.definition

    @property
    def definition_digest(self) -> str:
        return self.definition.definition_digest

    def resolve_instructions(
        self, node_id: str, *, generation_uuid: str,
    ) -> tuple[TextPart | ResolvedNodeRef, ...]:
        """Resolve typed targets only, with an explicit canonical generation UUID.

        This internal full-graph projection has no visibility filtering and must
        not be returned directly by a Host. Missing material (e.g. Tool) refuses.
        Literal Text is returned byte-for-byte, with no regex/path rewriting.
        """
        self.index.get(node_id)
        # Validate even empty/literal-only programs; never mint a generation.
        ManagedReference(self.definition.graph_id, generation_uuid, node_id)
        if node_id not in self.instructions:
            raise GraphError("compiler_contract_invalid", "Node has no instruction material")
        return tuple(
            part if isinstance(part, TextPart) else ResolvedNodeRef(
                ManagedReference(self.definition.graph_id, generation_uuid, part.target_id),
                part.label)
            for part in self.instructions[node_id]
        )


class GraphCompiler:
    """Compile an already decoded managed definition against an exact registry.

    Compiler contract/build, app dependency and environment are explicit required
    inputs, validated by ExecutionManifest (no defaults/latest/environment reads).
    A runtime graph, even one without Tools, requires a pinned registry. In
    disclosure mode registry is ignored completely, including its type/properties.
    """

    @staticmethod
    def compile(
        definition: GraphDefinition, *, registry: RegistryGeneration | None = None,
        compiler_contract_version: str, compiler_build: str,
        app_dependency_contract: AppDependencyContract,
        environment_compatibility: tuple[str, ...], locale: str,
        no_execution_registry_digest: str | None = None,
        execution_extensions: tuple[str, ...] = (),
    ) -> StructuralPlan:
        if not isinstance(definition, GraphDefinition):
            raise GraphError("compiler_contract_invalid", "Expected GraphDefinition")
        if not isinstance(execution_extensions, (tuple, list)) or execution_extensions:
            raise GraphError("compiler_contract_invalid", "Executable extensions are unsupported")
        selected_locale = normalize_locale(locale)
        resolved: dict[str, ResolvedBinding] = {}
        contracts: dict[str, PublicationContract] = {}
        if definition.mode == "disclosure_only":
            if no_execution_registry_digest is None:
                raise GraphError("compiler_contract_invalid",
                                 "Explicit no-execution registry digest is required")
            registry_digest = no_execution_registry_digest
        else:
            if not isinstance(registry, RegistryGeneration):
                raise GraphError("compiler_contract_invalid", "Pinned registry generation is required")
            if no_execution_registry_digest is not None:
                raise GraphError("compiler_contract_invalid", "No-execution identity in runtime mode")
            registry_digest = registry.registry_digest

        # Validate explicit manifest contracts before binding work. No invented
        # no-execution fields/sentinels: use the actual shared manifest validator.
        manifest = ExecutionManifest(compiler_contract_version, compiler_build,
            registry_digest, (), app_dependency_contract, environment_compatibility)

        if definition.mode == "runtime":
            assert isinstance(registry, RegistryGeneration)
            # Only a traversal schedule, NOT a second authority/index. Runtime
            # executable publications require evidence before GraphIndex.build.
            # Validate reachability/cycles/slots/publications with the real index
            # below, using freshly resolved evidence, never author's old evidence.
            pending = [root.node_id for root in reversed(definition.roots)]
            seen: set[str] = set()
            while pending:
                node_id = pending.pop()
                if node_id in seen or node_id not in definition.nodes:
                    raise GraphError("schema_invalid", "Invalid containment traversal")
                seen.add(node_id)
                node = definition.nodes[node_id]
                if node.kind == "tool":
                    if node.binding is None:
                        raise GraphError("binding_contract_required", "Runtime Tool requires binding")
                    binding = registry.resolve(node.binding, input_contract=node.input_contract)
                    resolved[node_id] = binding
                    contracts[node_id] = binding.publication_contract
                if node.members is not None:
                    pending.extend(child for _, _, child in reversed(node.members.placements()))

        index = GraphIndex.build(definition, publication_contracts=contracts)
        # Use the validated forest order, not node-array/ID lexical order. Repeated
        # descriptors remain repeated entries, keyed separately by node in plan.
        manifest = ExecutionManifest(compiler_contract_version, compiler_build,
            registry_digest, tuple(resolved[n].entry for n in index.preorder if n in resolved),
            app_dependency_contract, environment_compatibility)
        presentations: dict[str, EffectivePresentation] = {}
        instructions: dict[str, tuple[TextPart | NodeRefPart, ...]] = {}
        processes: dict[str, ProcessPlan] = {}
        for node in index.walk():
            presentation = index.presentation(node.node_id, selected_locale)
            presentations[node.node_id] = EffectivePresentation(
                presentation.title if presentation and presentation.title is not None else node.name,
                presentation.description if presentation and presentation.description is not None
                else node.description)
            if node.instruction_template is not None:
                instructions[node.node_id] = node.instruction_template.parts
            elif node.instructions is not None:
                # Construct through the model decoder, not a new text wire shape.
                instructions[node.node_id] = InstructionTemplate.from_json({
                    "parts": [{"kind": "Text", "text": node.instructions}]}).parts
            if node.members is not None:
                processes[node.node_id] = ProcessPlan(node.members.pre_process, node.members.post_process)
        return StructuralPlan(index, MappingProxyType(resolved), MappingProxyType(contracts),
            manifest, selected_locale, MappingProxyType(presentations),
            MappingProxyType(instructions), MappingProxyType(processes))