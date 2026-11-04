"""Describe one compiled Contexture application without running it.

The declaration objects, :class:`~contexture.core.model.index.Index`, and
:class:`~contexture.core.model.disclosure.Disclosure` each own a different
part of the truth.  This module joins those facts into one stable read model
for product UI, generated documentation, CI diffs, and developer tools.

It is deliberately separate from progressive disclosure.  A Node Catalog is
read by trusted application and developer code; it is never added to the MCP
gateway and never enters a model context unless an application explicitly
publishes it through a surface of its own.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol, Sequence, cast

from .core.constants import SEPARATOR
from .core.errors import ModelValidationError
from .core.model.disclosure import Disclosure
from .core.model.index import Index
from .core.model.node import CompiledGraph, ContextNode
from .core.model.role import Role
from .core.model.root_selection import SurfaceSelection, bound_surface_selection
from .core.model.skill import Skill
from .core.model.tool import Tool
from .core.presentation import (
    NodePresentation,
    Presentation,
    PresentationCatalog,
    resolve_presentation,
)
from .core.types import JsonObject, JsonValue


SCHEMA = "contexture.node-catalog/v1"


class CatalogView(str, Enum):
    """Which part of a compiled application the catalog describes."""

    APPLICATION = "application"
    MODEL = "model"
    SURFACE = "surface"


class CatalogDetail(str, Enum):
    """How much definition text and execution shape each node carries."""

    SUMMARY = "summary"
    DEFINITION = "definition"


class MemberSlot(str, Enum):
    """The semantic place a direct Role member occupies."""

    PRE_PROCESS = "pre_process"
    CHILD = "child"
    POST_PROCESS = "post_process"
    SKILL = "skill"
    TOOL = "tool"


class NodeMetadataContributor(Protocol):
    """Add one namespaced application read-model object to every node."""

    namespace: str

    def describe(
        self, ref: str, node: ContextNode, graph: CompiledGraph
    ) -> JsonObject | None:
        """Return JSON-safe application metadata, or ``None`` for this node."""


class CompiledCatalogSource(Protocol):
    """The compiled-application facts required by :class:`NodeCatalog`."""

    @property
    def name(self) -> str: ...

    @property
    def index(self) -> Index: ...

    @property
    def presentation(self) -> Presentation | PresentationCatalog: ...

    @property
    def prompt_root_refs(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class MemberDescriptor:
    ref: str
    kind: str
    slot: MemberSlot

    def __post_init__(self) -> None:
        _require_ref(self.ref, label="Catalog member ref")
        if self.kind not in _KINDS:
            raise ModelValidationError(f"Unknown catalog member kind {self.kind!r}.")
        try:
            slot = MemberSlot(self.slot)
        except ValueError as error:
            raise ModelValidationError(
                f"Unknown catalog member slot {self.slot!r}."
            ) from error
        object.__setattr__(self, "slot", slot)
        expected = {
            MemberSlot.PRE_PROCESS: Role.kind,
            MemberSlot.CHILD: Role.kind,
            MemberSlot.POST_PROCESS: Role.kind,
            MemberSlot.SKILL: Skill.kind,
            MemberSlot.TOOL: Tool.kind,
        }[slot]
        if self.kind != expected:
            raise ModelValidationError(
                f"Catalog member {self.ref!r} uses slot {slot.value!r} "
                f"with kind {self.kind!r}, expected {expected!r}."
            )

    def as_json(self) -> JsonObject:
        return {"ref": self.ref, "kind": self.kind, "slot": self.slot.value}


@dataclass(frozen=True, slots=True)
class PresentationDescriptor:
    title: str
    description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title:
            raise ModelValidationError("A catalog presentation title cannot be empty.")
        if self.description is not None and (
            not isinstance(self.description, str) or not self.description
        ):
            raise ModelValidationError(
                "A catalog presentation description cannot be empty."
            )

    def as_json(self) -> JsonObject:
        payload: JsonObject = {"title": self.title}
        if self.description is not None:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    authored_instructions: str
    effective_instructions: str
    pre_process_ref: str | None = None
    post_process_ref: str | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.authored_instructions, self.effective_instructions)
        ):
            raise ModelValidationError(
                "A catalog Role definition requires authored and effective instructions."
            )
        for label, ref in (
            ("pre-process ref", self.pre_process_ref),
            ("post-process ref", self.post_process_ref),
        ):
            if ref is not None:
                _require_ref(ref, label=f"Catalog Role {label}")

    def as_json(self) -> JsonObject:
        return {
            "authored_instructions": self.authored_instructions,
            "effective_instructions": self.effective_instructions,
            "pre_process_ref": self.pre_process_ref,
            "post_process_ref": self.post_process_ref,
        }


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    authored_instructions: str
    effective_instructions: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (self.authored_instructions, self.effective_instructions)
        ):
            raise ModelValidationError(
                "A catalog Skill definition requires authored and effective instructions."
            )

    def as_json(self) -> JsonObject:
        return {
            "authored_instructions": self.authored_instructions,
            "effective_instructions": self.effective_instructions,
        }


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    execution_available: bool
    read_only: bool | None = None
    input_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.execution_available, bool):
            raise ModelValidationError(
                "Tool execution availability must be a boolean."
            )
        if not self.execution_available and (
            self.read_only is not None or self.input_schema is not None
        ):
            raise ModelValidationError(
                "A disclosure-only Tool definition cannot claim read-only "
                "classification or an input schema."
            )
        if self.execution_available and (
            not isinstance(self.read_only, bool) or self.input_schema is None
        ):
            raise ModelValidationError(
                "An executable Tool definition needs read-only classification "
                "and an input schema."
            )
        if self.input_schema is not None:
            frozen = _freeze_object(self.input_schema, label="Tool input schema")
            object.__setattr__(self, "input_schema", frozen)

    def as_json(self) -> JsonObject:
        payload: JsonObject = {"execution_available": self.execution_available}
        if self.execution_available:
            assert self.read_only is not None and self.input_schema is not None
            payload["read_only"] = self.read_only
            payload["input_schema"] = cast(JsonObject, _thaw(self.input_schema))
        return payload


@dataclass(frozen=True, slots=True)
class NodeDescriptor:
    ref: str
    kind: str
    name: str
    description: str
    presentation: PresentationDescriptor | None
    plane: str
    parent_ref: str | None
    ordinal: int
    members: tuple[MemberDescriptor, ...] = ()
    uses: tuple[str, ...] = ()
    dependents: tuple[str, ...] = ()
    role: RoleDefinition | None = None
    skill: SkillDefinition | None = None
    tool: ToolDefinition | None = None
    extensions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_ref(self.ref, label="Catalog node ref")
        if self.kind not in {Role.kind, Skill.kind, Tool.kind}:
            raise ModelValidationError(f"Unknown catalog node kind {self.kind!r}.")
        if any(
            not isinstance(value, str) or not value
            for value in (self.name, self.description)
        ):
            raise ModelValidationError(
                f"Catalog node {self.ref!r} requires a name and description."
            )
        if self.plane not in {"model", "prompt"}:
            raise ModelValidationError(f"Unknown catalog plane {self.plane!r}.")
        if self.parent_ref is not None:
            _require_ref(self.parent_ref, label="Catalog parent ref")
            if self.parent_ref == self.ref:
                raise ModelValidationError(
                    f"Catalog node {self.ref!r} cannot be its own parent."
                )
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ModelValidationError("A catalog node ordinal cannot be negative.")
        if self.kind != Role.kind and self.members:
            raise ModelValidationError(
                f"Only a Role may contain catalog members; {self.ref!r} is {self.kind}."
            )
        definitions = {
            Role.kind: self.role,
            Skill.kind: self.skill,
            Tool.kind: self.tool,
        }
        populated = [kind for kind, value in definitions.items() if value is not None]
        if populated and populated != [self.kind]:
            raise ModelValidationError(
                f"Catalog node {self.ref!r} has a definition for the wrong kind."
            )
        for relation, refs in (
            ("member", tuple(member.ref for member in self.members)),
            ("uses", self.uses),
            ("dependent", self.dependents),
        ):
            if len(refs) != len(set(refs)):
                raise ModelValidationError(
                    f"Catalog node {self.ref!r} repeats a {relation} ref."
                )
            for ref in refs:
                _require_ref(ref, label=f"Catalog {relation} ref")
        frozen = _freeze_object(self.extensions, label="Node extensions")
        for namespace in frozen:
            _require_namespace(namespace)
        object.__setattr__(self, "extensions", frozen)

    def as_json(self) -> JsonObject:
        return {
            "ref": self.ref,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "presentation": (
                self.presentation.as_json() if self.presentation is not None else None
            ),
            "plane": self.plane,
            "parent_ref": self.parent_ref,
            "ordinal": self.ordinal,
            "members": [member.as_json() for member in self.members],
            "uses": list(self.uses),
            "dependents": list(self.dependents),
            "role": self.role.as_json() if self.role is not None else None,
            "skill": self.skill.as_json() if self.skill is not None else None,
            "tool": self.tool.as_json() if self.tool is not None else None,
            "extensions": cast(JsonObject, _thaw(self.extensions)),
        }


@dataclass(frozen=True, slots=True)
class ApplicationDescriptor:
    name: str
    mode: str
    view: CatalogView
    detail: CatalogDetail
    locale: str | None
    execution_bound: bool

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ModelValidationError("A catalog application name cannot be empty.")
        if self.mode not in {"runtime", "disclosure-only"}:
            raise ModelValidationError(f"Unknown catalog application mode {self.mode!r}.")
        try:
            view = CatalogView(self.view)
            detail = CatalogDetail(self.detail)
        except ValueError as error:
            raise ModelValidationError("Unknown catalog view or detail level.") from error
        object.__setattr__(self, "view", view)
        object.__setattr__(self, "detail", detail)
        if self.locale is not None and (
            not isinstance(self.locale, str) or not self.locale
        ):
            raise ModelValidationError(
                "A catalog locale must be null or a non-empty string."
            )
        if not isinstance(self.execution_bound, bool):
            raise ModelValidationError(
                "Catalog execution binding must be a boolean."
            )
        if (self.mode == "runtime") != self.execution_bound:
            raise ModelValidationError(
                "Catalog application mode and execution binding disagree."
            )

    def as_json(self) -> JsonObject:
        return {
            "name": self.name,
            "mode": self.mode,
            "view": self.view.value,
            "detail": self.detail.value,
            "locale": self.locale,
            "execution_bound": self.execution_bound,
        }


@dataclass(frozen=True, slots=True)
class NodeCatalogSnapshot:
    application: ApplicationDescriptor
    roots: tuple[str, ...]
    counts: Mapping[str, int]
    nodes: tuple[NodeDescriptor, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "roots", tuple(self.roots))
        object.__setattr__(self, "nodes", tuple(self.nodes))
        normalized_counts: dict[str, int] = {}
        for kind in _KINDS:
            value = self.counts.get(kind, 0)
            if type(value) is not int or value < 0:
                raise ModelValidationError(
                    f"Catalog count for {kind!r} must be a non-negative integer."
                )
            normalized_counts[kind] = value
        object.__setattr__(
            self,
            "counts",
            MappingProxyType(normalized_counts),
        )
        _validate_snapshot(self)

    @property
    def content_digest(self) -> str:
        return _digest(self._body())

    def _body(self) -> JsonObject:
        return {
            "schema": SCHEMA,
            "application": self.application.as_json(),
            "roots": list(self.roots),
            "counts": dict(self.counts),
            "nodes": [node.as_json() for node in self.nodes],
        }

    def as_json(self) -> JsonObject:
        return {**self._body(), "content_digest": self.content_digest}

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize deterministically for artifacts and cross-language readers."""

        return json.dumps(
            _canonical(self.as_json()),
            ensure_ascii=False,
            indent=indent,
            separators=(",", ":") if indent is None else None,
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class NodeCatalog:
    """A queryable, immutable catalog over one compiled graph view."""

    snapshot_value: NodeCatalogSnapshot
    _by_ref: Mapping[str, NodeDescriptor] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_ref", MappingProxyType(dict(self._by_ref)))

    @classmethod
    def of(
        cls,
        compiled: CompiledCatalogSource,
        *,
        view: CatalogView | str,
        detail: CatalogDetail | str = CatalogDetail.SUMMARY,
        locale: str | None = None,
        surface_selection: SurfaceSelection | None = None,
        contributors: Iterable[NodeMetadataContributor] = (),
    ) -> "NodeCatalog":
        """Assemble one catalog without opening application dependencies."""

        normalized_view = CatalogView(view)
        normalized_detail = CatalogDetail(detail)
        if normalized_view is CatalogView.SURFACE and surface_selection is None:
            raise ModelValidationError(
                "A surface Node Catalog requires an explicit SurfaceSelection."
            )
        if normalized_view is not CatalogView.SURFACE and surface_selection is not None:
            raise ModelValidationError(
                "SurfaceSelection is valid only for a surface Node Catalog."
            )

        # Catalog construction is an explicit application operation, not a
        # request continuation.  A task-local surface selection left by an HTTP
        # adapter must never silently narrow generated artifacts or debug data.
        with bound_surface_selection(SurfaceSelection.all()):
            resolved_locale, presentation = resolve_presentation(
                compiled.presentation, locale
            )
            disclosure = Disclosure(
                compiled.index,
                frozenset(compiled.prompt_root_refs),
                node_presentations=presentation.nodes,
            )
            if normalized_view is CatalogView.APPLICATION:
                active_view = disclosure.unrestricted()
                included = {ref for ref, _ in compiled.index.walk()}
                roots = tuple(
                    compiled.index.ref_of(root) for root in compiled.index.roots
                )
                selected_roots: frozenset[str] = frozenset()
            else:
                active_view = disclosure
                if normalized_view is CatalogView.SURFACE:
                    assert surface_selection is not None
                    active_view = disclosure.select(surface_selection)
                included = {
                    ref
                    for ref, _ in compiled.index.walk()
                    if active_view.model_can_see(ref)
                }
                roots = tuple(
                    active_view.ref_of(root)
                    for root in active_view.roots
                    if active_view.ref_of(root) in included
                )
                selected = active_view.effective_selection
                selected_roots = selected.names or frozenset()

            normalized_contributors = _contributors(contributors)
            descriptors: list[NodeDescriptor] = []
            for ref, node in compiled.index.walk():
                if ref not in included:
                    continue
                descriptors.append(
                    _descriptor(
                        compiled.index,
                        active_view,
                        ref,
                        node,
                        included=included,
                        selected_roots=selected_roots,
                        prompt_roots=frozenset(compiled.prompt_root_refs),
                        detail=normalized_detail,
                        presentation=presentation.nodes.get(ref),
                        contributors=normalized_contributors,
                    )
                )

            counts = {
                kind: sum(1 for descriptor in descriptors if descriptor.kind == kind)
                for kind in _KINDS
            }
            application = ApplicationDescriptor(
                name=compiled.name,
                mode="runtime" if compiled.index.is_bound else "disclosure-only",
                view=normalized_view,
                detail=normalized_detail,
                locale=resolved_locale,
                execution_bound=compiled.index.is_bound,
            )
            snapshot = NodeCatalogSnapshot(
                application=application,
                roots=roots,
                counts=counts,
                nodes=tuple(descriptors),
            )
        return cls(snapshot, {node.ref: node for node in descriptors})

    @property
    def roots(self) -> tuple[str, ...]:
        return self.snapshot_value.roots

    def get(self, ref: str) -> NodeDescriptor:
        try:
            return self._by_ref[ref]
        except KeyError as error:
            raise ModelValidationError(
                f"Reference {ref!r} is not present in this Node Catalog view."
            ) from error

    def children(self, ref: str) -> tuple[NodeDescriptor, ...]:
        node = self.get(ref)
        return tuple(self.get(member.ref) for member in node.members)

    def list(
        self, *, kind: str | None = None, query: str = ""
    ) -> tuple[NodeDescriptor, ...]:
        if kind is not None and kind not in _KINDS:
            raise ModelValidationError(f"Unknown catalog node kind {kind!r}.")
        wanted = query.strip().casefold()
        return tuple(
            node
            for node in self.snapshot_value.nodes
            if (kind is None or node.kind == kind)
            and (not wanted or wanted in _search_text(node).casefold())
        )

    def snapshot(self) -> NodeCatalogSnapshot:
        return self.snapshot_value


_KINDS = (Role.kind, Skill.kind, Tool.kind)


def _require_ref(ref: str, *, label: str) -> None:
    if (
        not isinstance(ref, str)
        or not ref
        or ref.startswith(SEPARATOR)
        or ref.endswith(SEPARATOR)
        or any(not part for part in ref.split(SEPARATOR))
    ):
        raise ModelValidationError(
            f"{label} must be a non-empty canonical Contexture ref."
        )


def _require_namespace(namespace: str) -> None:
    if (
        "/" not in namespace
        or namespace.startswith("/")
        or namespace.endswith("/")
        or any(not part.strip() for part in namespace.split("/"))
    ):
        raise ModelValidationError(
            f"Catalog extension namespace {namespace!r} must contain an owner "
            "prefix and '/', for example 'acme/source'."
        )


def _validate_snapshot(snapshot: NodeCatalogSnapshot) -> None:
    """Enforce graph invariants JSON Schema cannot express across nodes."""

    refs = tuple(node.ref for node in snapshot.nodes)
    if len(refs) != len(set(refs)):
        raise ModelValidationError("A Node Catalog cannot contain duplicate refs.")
    by_ref = {node.ref: node for node in snapshot.nodes}
    if len(snapshot.roots) != len(set(snapshot.roots)):
        raise ModelValidationError("A Node Catalog cannot repeat a root ref.")
    unknown_roots = set(snapshot.roots) - set(by_ref)
    if unknown_roots:
        raise ModelValidationError(
            f"Node Catalog roots are absent from nodes: {sorted(unknown_roots)!r}."
        )
    parentless = {node.ref for node in snapshot.nodes if node.parent_ref is None}
    if parentless != set(snapshot.roots):
        raise ModelValidationError(
            "Node Catalog roots must be exactly the nodes with no visible parent."
        )

    actual_counts = {
        kind: sum(node.kind == kind for node in snapshot.nodes) for kind in _KINDS
    }
    if dict(snapshot.counts) != actual_counts:
        raise ModelValidationError(
            f"Node Catalog counts disagree with nodes: {dict(snapshot.counts)!r} "
            f"!= {actual_counts!r}."
        )

    for ordinal, ref in enumerate(snapshot.roots):
        if by_ref[ref].ordinal != ordinal:
            raise ModelValidationError(
                f"Catalog root {ref!r} has ordinal {by_ref[ref].ordinal}, "
                f"expected {ordinal}."
            )

    for node in snapshot.nodes:
        definitions = {
            Role.kind: node.role,
            Skill.kind: node.skill,
            Tool.kind: node.tool,
        }
        populated = [kind for kind, value in definitions.items() if value is not None]
        expected = [] if snapshot.application.detail is CatalogDetail.SUMMARY else [node.kind]
        if populated != expected:
            raise ModelValidationError(
                f"Catalog node {node.ref!r} definitions do not match "
                f"{snapshot.application.detail.value!r} detail."
            )

        if node.parent_ref is not None:
            parent = by_ref.get(node.parent_ref)
            if parent is None:
                raise ModelValidationError(
                    f"Catalog node {node.ref!r} has missing parent {node.parent_ref!r}."
                )
            if parent.kind != Role.kind:
                raise ModelValidationError(
                    f"Catalog node {node.ref!r} has non-Role parent {parent.ref!r}."
                )
            matching = [member for member in parent.members if member.ref == node.ref]
            if len(matching) != 1:
                raise ModelValidationError(
                    f"Catalog parent {parent.ref!r} must name {node.ref!r} exactly once."
                )
            if node.ordinal != parent.members.index(matching[0]):
                raise ModelValidationError(
                    f"Catalog node {node.ref!r} ordinal disagrees with parent members."
                )
            if node.plane != parent.plane:
                raise ModelValidationError(
                    f"Catalog node {node.ref!r} changes plane below its parent."
                )

        for position, member in enumerate(node.members):
            target = by_ref.get(member.ref)
            if target is None:
                raise ModelValidationError(
                    f"Catalog member {member.ref!r} of {node.ref!r} is absent."
                )
            if target.parent_ref != node.ref or target.kind != member.kind:
                raise ModelValidationError(
                    f"Catalog member {member.ref!r} disagrees with its target node."
                )
            if target.ordinal != position:
                raise ModelValidationError(
                    f"Catalog member {member.ref!r} ordinal is not contiguous."
                )

        if node.role is not None:
            slots = {member.slot: member.ref for member in node.members}
            if node.role.pre_process_ref != slots.get(MemberSlot.PRE_PROCESS):
                raise ModelValidationError(
                    f"Catalog Role {node.ref!r} pre-process definition disagrees "
                    "with its member slot."
                )
            if node.role.post_process_ref != slots.get(MemberSlot.POST_PROCESS):
                raise ModelValidationError(
                    f"Catalog Role {node.ref!r} post-process definition disagrees "
                    "with its member slot."
                )

        if node.tool is not None and (
            node.tool.execution_available != snapshot.application.execution_bound
        ):
            raise ModelValidationError(
                f"Catalog Tool {node.ref!r} execution availability disagrees "
                "with the application mode."
            )

        for target_ref in node.uses:
            target = by_ref.get(target_ref)
            if target is None or node.ref not in target.dependents:
                raise ModelValidationError(
                    f"Catalog uses edge {node.ref!r} -> {target_ref!r} is not reciprocal."
                )
        for source_ref in node.dependents:
            source = by_ref.get(source_ref)
            if source is None or node.ref not in source.uses:
                raise ModelValidationError(
                    f"Catalog dependent edge {source_ref!r} -> {node.ref!r} is not reciprocal."
                )


def _descriptor(
    index: Index,
    disclosure: Disclosure,
    ref: str,
    node: ContextNode,
    *,
    included: set[str],
    selected_roots: frozenset[str],
    prompt_roots: frozenset[str],
    detail: CatalogDetail,
    presentation: NodePresentation | None,
    contributors: tuple[NodeMetadataContributor, ...],
) -> NodeDescriptor:
    parent = index.parent_of(node)
    parent_ref = index.ref_of(parent) if parent is not None else None
    if ref in selected_roots or parent_ref not in included:
        parent_ref = None
    if parent_ref is None:
        if selected_roots:
            siblings = [
                candidate for candidate in selected_roots if candidate in included
            ]
            siblings.sort(key=lambda candidate: _walk_position(index, candidate))
        else:
            siblings = [
                index.ref_of(root)
                for root in index.roots
                if index.ref_of(root) in included
            ]
    else:
        siblings = [
            index.ref_of(member)
            for member in cast(Role, parent).members()
            if index.ref_of(member) in included
        ]
    ordinal = siblings.index(ref)

    members = _members(index, node, included)
    root = ref.split(SEPARATOR, 1)[0]
    extensions: dict[str, JsonObject] = {}
    for contributor in contributors:
        value = contributor.describe(ref, node, index)
        if value is not None:
            extensions[contributor.namespace] = value

    role: RoleDefinition | None = None
    skill: SkillDefinition | None = None
    tool: ToolDefinition | None = None
    if detail is CatalogDetail.DEFINITION:
        active = disclosure.open(ref)
        if isinstance(node, Role):
            role = RoleDefinition(
                authored_instructions=node.instructions,
                effective_instructions=str(active["instructions"]),
                pre_process_ref=cast(str | None, active.get("pre_process")),
                post_process_ref=cast(str | None, active.get("post_process")),
            )
        elif isinstance(node, Skill):
            skill = SkillDefinition(
                authored_instructions=node.instructions,
                effective_instructions=str(active["instructions"]),
            )
        elif isinstance(node, Tool):
            if index.is_bound:
                tool = ToolDefinition(
                    execution_available=True,
                    read_only=node.read_only,
                    input_schema=disclosure.schema_of(node),
                )
            else:
                tool = ToolDefinition(execution_available=False)

    return NodeDescriptor(
        ref=ref,
        kind=node.kind,
        name=node.name,
        description=node.description,
        presentation=(
            PresentationDescriptor(presentation.title, presentation.description)
            if presentation is not None
            else None
        ),
        plane="prompt" if root in prompt_roots else "model",
        parent_ref=parent_ref,
        ordinal=ordinal,
        members=members,
        uses=tuple(target for target in node.uses if target in included),
        dependents=tuple(
            source for source in index.dependents_of(ref) if source in included
        ),
        role=role,
        skill=skill,
        tool=tool,
        extensions=extensions,
    )


def _members(
    index: Index, node: ContextNode, included: set[str]
) -> tuple[MemberDescriptor, ...]:
    if not isinstance(node, Role):
        return ()
    described: list[MemberDescriptor] = []
    for member in node.members():
        ref = index.ref_of(member)
        if ref not in included:
            continue
        if member is node.pre_process:
            slot = MemberSlot.PRE_PROCESS
        elif member is node.post_process:
            slot = MemberSlot.POST_PROCESS
        elif member in node.children:
            slot = MemberSlot.CHILD
        elif member in node.skills:
            slot = MemberSlot.SKILL
        else:
            slot = MemberSlot.TOOL
        described.append(MemberDescriptor(ref=ref, kind=member.kind, slot=slot))
    return tuple(described)


def _contributors(
    values: Iterable[NodeMetadataContributor],
) -> tuple[NodeMetadataContributor, ...]:
    contributors = tuple(values)
    seen: set[str] = set()
    for contributor in contributors:
        namespace = str(getattr(contributor, "namespace", ""))
        _require_namespace(namespace)
        if namespace in seen:
            raise ModelValidationError(
                f"Catalog extension namespace {namespace!r} is registered twice."
            )
        seen.add(namespace)
    return contributors


def _search_text(node: NodeDescriptor) -> str:
    fields = [node.ref, node.name, node.description, *node.uses, *node.dependents]
    if node.presentation is not None:
        fields.extend(
            [node.presentation.title, node.presentation.description or ""]
        )
    if node.role is not None:
        fields.extend(
            [node.role.authored_instructions, node.role.effective_instructions]
        )
    if node.skill is not None:
        fields.extend(
            [node.skill.authored_instructions, node.skill.effective_instructions]
        )
    if node.tool is not None:
        fields.append(json.dumps(node.tool.as_json(), ensure_ascii=False, sort_keys=True))
    fields.append(json.dumps(_thaw(node.extensions), ensure_ascii=False, sort_keys=True))
    return " ".join(fields)


def _walk_position(index: Index, ref: str) -> int:
    for position, (candidate, _) in enumerate(index.walk()):
        if candidate == ref:
            return position
    raise AssertionError(f"compiled ref {ref!r} disappeared")


def _freeze_object(value: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelValidationError(f"{label} must be a JSON object.")
    frozen = _freeze(value, label=label)
    assert isinstance(frozen, Mapping)
    return frozen


def _freeze(value: Any, *, label: str) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModelValidationError(f"{label} contains a non-finite number.")
        return int(value) if value.is_integer() else value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ModelValidationError(f"{label} contains a non-string key.")
            copied[key] = _freeze(item, label=label)
        return MappingProxyType(copied)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, label=label) for item in value)
    raise ModelValidationError(
        f"{label} contains non-JSON value {type(value).__name__}."
    )


def _thaw(value: Any) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return cast(JsonValue, value)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModelValidationError("Catalog JSON contains a non-finite number.")
        return int(value) if value.is_integer() else value
    return value


def _digest(value: Mapping[str, Any]) -> str:
    body = json.dumps(
        _canonical(value), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def canonical_digest(value: Mapping[str, Any]) -> str:
    """Return the Node Catalog canonical SHA-256 form for a JSON object.

    Application envelopes may use this helper to cover catalog snapshots with
    the exact same key ordering and number normalization as the framework
    contract, without importing or copying its private serializer.
    """

    return _digest(_freeze_object(value, label="Digest input"))


__all__ = [
    "ApplicationDescriptor",
    "CatalogDetail",
    "CatalogView",
    "MemberDescriptor",
    "MemberSlot",
    "NodeCatalog",
    "NodeCatalogSnapshot",
    "NodeDescriptor",
    "NodeMetadataContributor",
    "PresentationDescriptor",
    "RoleDefinition",
    "SCHEMA",
    "SkillDefinition",
    "ToolDefinition",
    "canonical_digest",
]
