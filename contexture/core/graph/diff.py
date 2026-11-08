"""Stable-ID definition diff, distinct from explicitly supplied runtime views.

DefinitionDiff is the schema's five groups, not an execution/behavior claim.
execution_diff compares pinned ExecutionManifests and surface_diff compiled
StructuralPlans; equal definition text never proves equal execution/surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .canonical import FrozenJSON, canonical_equal, canonical_json, freeze_json, thaw_json
from .errors import GraphError
from .events import Wire, optional
from .model import GraphDefinition

if TYPE_CHECKING:
    from .bindings import ExecutionManifest
    from .compiler import StructuralPlan

GROUPS = ("nodes", "membership", "uses", "publications", "locales")


def _pointer(key: str) -> str:
    return "/" + key.replace("~", "~0").replace("/", "~1")


def _entry(identity: str, field: str, before: Mapping[str, object],
           after: Mapping[str, object], key: str) -> Wire | None:
    a, b = optional(before, key), optional(after, key)
    if canonical_equal(a, b):
        return None
    return {"id": identity, "field": field, "before": a, "after": b}


@dataclass(frozen=True, slots=True)
class DefinitionDiff:
    """Immutable detached schema DefinitionDiff. Group and ID/field order fixed."""

    value: Mapping[str, FrozenJSON]

    def __post_init__(self) -> None:
        if set(self.value) != set(GROUPS):
            raise GraphError("schema_invalid", "DefinitionDiff requires its five declared groups")
        object.__setattr__(self, "value", cast(Mapping[str, FrozenJSON], freeze_json(self.value)))

    def as_json(self) -> Wire:
        return cast(Wire, thaw_json(self.value))

    @property
    def empty(self) -> bool:
        return all(not self.value[k] for k in GROUPS)

    def entries(self, group: str) -> list[Wire]:
        """Detached entries of one group: nodes, membership, uses, publications, locales."""
        if group not in GROUPS:
            raise GraphError("schema_invalid", "Unknown DefinitionDiff group")
        return cast(list[Wire], thaw_json(self.value[group]))


def definition_diff(base: GraphDefinition, candidate: GraphDefinition) -> DefinitionDiff:
    """Net endpoint diff. Whole relation lists avoid ordinal-shift fake moves.

    IDs in graph-level entries are graph_id; node entries use node_id and
    publication entries use publication_id. JSON pointers disambiguate fields.
    Full presentations includes catalog/entry order, empty catalogs and default.
    """
    if base.graph_id != candidate.graph_id:
        raise GraphError("schema_invalid", "Cannot align different graph identities")
    groups: dict[str, list[Wire]] = {k: [] for k in GROUPS}
    a, b = base.header(), candidate.header()
    for field in ("schema", "semantic_version", "application_name", "mode", "extensions"):
        entry = _entry(base.graph_id, _pointer(field), a, b, field)
        if entry:
            groups["nodes"].append(entry)
    entry = _entry(base.graph_id, "/roots", a, b, "roots")
    if entry:
        groups["membership"].append(entry)
    # Unchanged (usually shared) nodes contribute nothing; expand only the rest.
    changed = [n for n in sorted(base.nodes.keys() | candidate.nodes.keys())
               if n not in base.nodes or n not in candidate.nodes
               or not base.nodes[n].same(candidate.nodes[n])]
    an = {n: base.nodes[n].as_json() for n in changed if n in base.nodes}
    bn = {n: candidate.nodes[n].as_json() for n in changed if n in candidate.nodes}
    for identity in changed:
        if identity not in an or identity not in bn:
            entry = _entry(identity, "", {"node": an[identity]} if identity in an else {},
                           {"node": bn[identity]} if identity in bn else {}, "node")
            if entry:
                groups["nodes"].append(entry)
            continue
        for field in sorted(an[identity].keys() | bn[identity].keys()):
            entry = _entry(identity, _pointer(field), an[identity], bn[identity], field)
            if entry:
                group = "membership" if field == "members" else "uses" if field == "uses" else "nodes"
                groups[group].append(entry)
    # The ordered whole arrays are exact and include reordering. Publication
    # objects also align by ID for consumers interested in a particular object.
    entry = _entry(base.graph_id, "/publications", a, b, "publications")
    if entry:
        groups["publications"].append(entry)
    ap = {p.publication_id: p.as_json() for p in base.publications}
    bp = {p.publication_id: p.as_json() for p in candidate.publications}
    for identity in sorted(ap.keys() | bp.keys()):
        entry = _entry(identity, "", {"publication": ap[identity]} if identity in ap else {},
                       {"publication": bp[identity]} if identity in bp else {}, "publication")
        if entry:
            groups["publications"].append(entry)
    entry = _entry(base.graph_id, "/presentations", a, b, "presentations")
    if entry:
        groups["locales"].append(entry)
    for values in groups.values():
        values.sort(key=lambda e: (cast(str, e["id"]), cast(str, e["field"])))
    return DefinitionDiff(cast(Mapping[str, FrozenJSON], freeze_json(groups)))


def _changes(before: Mapping[str, object], after: Mapping[str, object]) -> list[Wire]:
    return [{"id": key, "before": optional(before, key), "after": optional(after, key)}
            for key in sorted(before.keys() | after.keys())
            if not canonical_equal(optional(before, key), optional(after, key))]


def execution_diff(before: ExecutionManifest, after: ExecutionManifest) -> Mapping[str, FrozenJSON]:
    """What changed in what actually executes: compiler, registry, app/environment
    and per-binding entries (grouped by binding_id, so an upgrade shows v1 -> v2).

    Equal definitions never prove equal execution; compare pinned manifests.
    """
    from .bindings import ExecutionManifest as _Manifest

    if not isinstance(before, _Manifest) or not isinstance(after, _Manifest):
        raise GraphError("schema_invalid", "Execution diff requires two ExecutionManifests")
    a, b = before.as_json(), after.as_json()
    derived = ("binding_entries", "manifest_digest")
    fields = {k: v for k, v in a.items() if k not in derived}
    later = {k: v for k, v in b.items() if k not in derived}

    def grouped(manifest: ExecutionManifest) -> dict[str, object]:
        result: dict[str, list[object]] = {}
        for entry in manifest.binding_entries:
            result.setdefault(entry.binding.binding_id, []).append(entry.as_json())
        return {k: sorted(v, key=canonical_json) for k, v in result.items()}

    changes = {"fields": _changes(fields, later), "bindings": _changes(grouped(before), grouped(after))}
    return cast(Mapping[str, FrozenJSON], freeze_json({
        "kind": "execution", "equal": before.manifest_digest == after.manifest_digest,
        "before_digest": before.manifest_digest, "after_digest": after.manifest_digest, **changes}))


def surface_diff(before: StructuralPlan, after: StructuralPlan) -> Mapping[str, FrozenJSON]:
    """What changed in the effective agent-facing surface of two compiled plans:
    selected locale, effective presentations, typed instruction programs and
    process designations, aligned by stable node ID (not display path)."""
    from .compiler import StructuralPlan as _Plan

    if not isinstance(before, _Plan) or not isinstance(after, _Plan):
        raise GraphError("schema_invalid", "Surface diff requires two compiled StructuralPlans")
    if before.definition.graph_id != after.definition.graph_id:
        raise GraphError("schema_invalid", "Cannot align different graph identities")

    def projection(plan: StructuralPlan) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for identity in plan.definition.nodes:
            value: dict[str, object] = {"path": plan.index.path(identity),
                                        "plane": plan.index.locations[identity].plane}
            if identity in plan.presentations:
                p = plan.presentations[identity]
                value["presentation"] = {"title": p.title, "description": p.description}
            if identity in plan.instructions:
                value["instructions"] = [part.as_json() for part in plan.instructions[identity]]
            if identity in plan.processes:
                process = plan.processes[identity]
                value["process"] = {"pre_process": process.pre_process, "post_process": process.post_process}
            result[identity] = value
        return result

    a, b = projection(before), projection(after)
    nodes: list[Wire] = []
    for identity in sorted(a.keys() | b.keys()):
        if identity not in a or identity not in b:
            nodes.append({"id": identity, "field": "", "before": optional(a, identity),
                          "after": optional(b, identity)})
            continue
        nodes.extend({"id": identity, "field": change["id"], "before": change["before"],
                      "after": change["after"]} for change in _changes(a[identity], b[identity]))
    locale = [] if before.selected_locale == after.selected_locale else [
        {"before": before.selected_locale, "after": after.selected_locale}]
    return cast(Mapping[str, FrozenJSON], freeze_json({
        "kind": "surface", "equal": not nodes and not locale, "locale": locale, "nodes": nodes}))
