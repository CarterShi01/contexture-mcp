"""Stable-ID definition diff, distinct from explicitly supplied runtime views.

DefinitionDiff is the schema's five groups, not an execution/behavior claim.
Projection comparisons require their real inputs; text is not semantic proof.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from .canonical import FrozenJSON, canonical_equal, freeze_json, thaw_json
from .errors import GraphError
from .events import Wire, optional
from .model import GraphDefinition

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


def definition_diff(base: GraphDefinition, candidate: GraphDefinition) -> DefinitionDiff:
    """Net endpoint diff. Whole relation lists avoid ordinal-shift fake moves.

    IDs in graph-level entries are graph_id; node entries use node_id and
    publication entries use publication_id. JSON pointers disambiguate fields.
    Full presentations includes catalog/entry order, empty catalogs and default.
    """
    if base.graph_id != candidate.graph_id:
        raise GraphError("schema_invalid", "Cannot align different graph identities")
    groups: dict[str, list[Wire]] = {k: [] for k in GROUPS}
    a, b = base.as_json(), candidate.as_json()
    for field in ("schema", "semantic_version", "application_name", "mode", "extensions"):
        entry = _entry(base.graph_id, _pointer(field), a, b, field)
        if entry:
            groups["nodes"].append(entry)
    entry = _entry(base.graph_id, "/roots", a, b, "roots")
    if entry:
        groups["membership"].append(entry)
    an = {n: v.as_json() for n, v in base.nodes.items()}
    bn = {n: v.as_json() for n, v in candidate.nodes.items()}
    for identity in sorted(an.keys() | bn.keys()):
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


# Convenient transaction-facing spelling; candidate wire acceptance is separate.
diff = definition_diff


def _projection_diff(before: object, after: object, kind: str) -> Mapping[str, FrozenJSON]:
    a, b = freeze_json(before), freeze_json(after)
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        raise GraphError("schema_invalid", "Explicit normalized projection objects are required")
    return cast(Mapping[str, FrozenJSON], freeze_json({
        "kind": kind, "equal": canonical_equal(a, b), "before": a, "after": b,
    }))


def behavior_diff(before: object, after: object) -> Mapping[str, FrozenJSON]:
    """Compare caller's behavior projections/evaluation evidence, not graph text."""
    return _projection_diff(before, after, "behavior")


def surface_diff(before: object, after: object) -> Mapping[str, FrozenJSON]:
    """Compare actual rendered locale/selection/scope surface projections."""
    return _projection_diff(before, after, "surface")


def execution_diff(before: object, after: object) -> Mapping[str, FrozenJSON]:
    """Compare explicit pinned manifests incl. registry/compiler/dependencies."""
    return _projection_diff(before, after, "execution")