"""Three-way merge of graph definitions by stable identity, without I/O.

``merge(base, ours, theirs)`` aligns nodes, publications and presentation
entries by ID (renames and moves keep identity) and merges field by field:

* changed on one side only -> that side; changed identically -> accepted;
* changed differently, or deleted on one side and modified on the other ->
  a conflict, never silently resolved;
* ordered relations (members per slot, uses, roots, publication and catalog
  order) merge membership three-way, keep the one side whose relative order
  changed (both changed differently -> conflict) and place each side's
  insertions after their nearest surviving predecessor (ours first);
* the merged candidate must pass full forest validation; failures are
  reported as a ``structure`` conflict, never as a partial graph.

Callers resolve conflicts explicitly by key with ``"ours"``, ``"theirs"`` or
``{"value": ...}`` and merge again. The result is only a candidate: commit it
through an ordinary previewed, admitted transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Union, cast

from .canonical import canonical_json, freeze_json, thaw_json
from .errors import GraphError
from .model import GraphDefinition

Wire = dict[str, object]
Resolution = Union[Literal["ours", "theirs"], Mapping[str, object]]


class _Absent:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<absent>"


ABSENT = _Absent()


def _same(a: object, b: object) -> bool:
    if a is ABSENT or b is ABSENT:
        return a is b
    return canonical_json(a) == canonical_json(b)


@dataclass(frozen=True, slots=True)
class MergeConflict:
    key: str
    kind: Literal["value", "delete_modify", "order", "structure"]
    base: object
    ours: object
    theirs: object
    message: str = ""

    def as_json(self) -> Wire:
        def wire(v: object) -> Wire:
            return {"present": False} if v is ABSENT else {"present": True, "value": v}
        return {"key": self.key, "kind": self.kind, "base": wire(self.base),
                "ours": wire(self.ours), "theirs": wire(self.theirs), "message": self.message}


@dataclass(frozen=True, slots=True)
class MergeResult:
    candidate: GraphDefinition | None
    conflicts: tuple[MergeConflict, ...]

    @property
    def clean(self) -> bool:
        return not self.conflicts


class _Merger:
    def __init__(self, resolutions: Mapping[str, Resolution]):
        self.resolutions = dict(resolutions)
        self.conflicts: list[MergeConflict] = []
        self.used: set[str] = set()

    def resolved(self, key: str, ours: object, theirs: object) -> tuple[bool, object]:
        choice = self.resolutions.get(key)
        if choice is None:
            return False, None
        self.used.add(key)
        if choice == "ours":
            return True, ours
        if choice == "theirs":
            return True, theirs
        if isinstance(choice, Mapping) and set(choice) == {"value"}:
            return True, thaw_json(freeze_json(choice["value"]))
        raise GraphError("schema_invalid", f"Invalid merge resolution: {key}")

    def value(self, key: str, base: object, ours: object, theirs: object,
              kind: Literal["value", "delete_modify"] = "value") -> object:
        if _same(ours, theirs):
            return ours
        if _same(ours, base):
            return theirs
        if _same(theirs, base):
            return ours
        done, chosen = self.resolved(key, ours, theirs)
        if done:
            return chosen
        self.conflicts.append(MergeConflict(key, kind, base, ours, theirs))
        return ours

    def sequence(self, key: str, base: Sequence[str], ours: Sequence[str],
                 theirs: Sequence[str]) -> list[str]:
        b, o, t = list(base), list(ours), list(theirs)
        if o == t or t == b:
            return o
        if o == b:
            return t
        done, chosen = self.resolved(key, o, t)
        if done:
            return list(cast(Sequence[str], chosen))
        sb, so, st = set(b), set(o), set(t)
        member = {x: ((x in so) if (x in st) == (x in sb) else (x in st)) for x in (*b, *o, *t)}
        retained = [x for x in b if member[x]]
        keep = set(retained)
        order_o = [x for x in o if x in keep]
        order_t = [x for x in t if x in keep]
        if order_o != retained and order_t != retained and order_o != order_t:
            self.conflicts.append(MergeConflict(key, "order", b, o, t))
            return o
        result = order_t if order_o == retained else order_o
        for side in (o, t):
            for i, x in enumerate(side):
                if x in sb or not member[x] or x in result:
                    continue
                anchor = next((side[j] for j in range(i - 1, -1, -1) if side[j] in result), None)
                result.insert(0 if anchor is None else result.index(anchor) + 1, x)
        return result

    def keyed(self, key: str, base: Sequence[Wire], ours: Sequence[Wire], theirs: Sequence[Wire],
              identity: str) -> list[Wire]:
        """Ordered objects with an identity field: merge values by ID, order as a sequence."""
        def index(items: Sequence[Wire]) -> dict[str, Wire]:
            return {cast(str, item[identity]): item for item in items}
        bi, oi, ti = index(base), index(ours), index(theirs)
        values: dict[str, object] = {}
        for item_id in dict.fromkeys((*bi, *oi, *ti)):
            b, o, t = (bi.get(item_id, ABSENT), oi.get(item_id, ABSENT), ti.get(item_id, ABSENT))
            kind: Literal["value", "delete_modify"] = (
                "delete_modify" if ABSENT in (o, t) and b is not ABSENT and not (o is ABSENT and t is ABSENT)
                else "value")
            values[item_id] = self.value(f"{key}:{item_id}", b, o, t, kind)
        order = self.sequence(f"{key}.order", list(bi), [i for i in oi], [i for i in ti])
        return [cast(Wire, values[i]) for i in order if values.get(i, ABSENT) is not ABSENT]


_SLOTS = ("pre_process", "children", "post_process", "skills", "tools")
_LISTS = ("children", "skills", "tools")


def merge(base: GraphDefinition, ours: GraphDefinition, theirs: GraphDefinition, *,
          resolutions: Mapping[str, Resolution] | None = None) -> MergeResult:
    if len({base.graph_id, ours.graph_id, theirs.graph_id}) != 1:
        raise GraphError("schema_invalid", "Merge requires one graph lineage")
    m = _Merger(resolutions or {})
    b, o, t = base.as_json(), ours.as_json(), theirs.as_json()
    result: Wire = {"schema": o["schema"], "semantic_version": o["semantic_version"],
                    "graph_id": o["graph_id"]}
    for field in ("application_name", "mode", "extensions"):
        result[field] = m.value(f"graph:{field}", b[field], o[field], t[field])
    bn = {cast(str, n["node_id"]): n for n in cast(list[Wire], b["nodes"])}
    on = {cast(str, n["node_id"]): n for n in cast(list[Wire], o["nodes"])}
    tn = {cast(str, n["node_id"]): n for n in cast(list[Wire], t["nodes"])}
    nodes: list[Wire] = []
    for node_id in sorted(set(bn) | set(on) | set(tn)):
        nb, no, nt = bn.get(node_id, ABSENT), on.get(node_id, ABSENT), tn.get(node_id, ABSENT)
        if no is ABSENT or nt is ABSENT:
            # Existence: delete on one side vs modification on the other conflicts.
            present = m.value(f"node:{node_id}", nb, no, nt,
                              "delete_modify" if nb is not ABSENT and (no is not ABSENT or nt is not ABSENT)
                              else "value")
            if present is not ABSENT and present is not None:
                nodes.append(cast(Wire, present))
            continue
        merged: Wire = {}
        nb_w = cast(Wire, nb) if nb is not ABSENT else {}
        no_w, nt_w = cast(Wire, no), cast(Wire, nt)
        for field in sorted((set(nb_w) | set(no_w) | set(nt_w)) - {"members", "uses"}):
            value = m.value(f"node:{node_id}:{field}", nb_w.get(field, ABSENT),
                            no_w.get(field, ABSENT), nt_w.get(field, ABSENT))
            if value is not ABSENT:
                merged[field] = value
        merged["uses"] = m.sequence(f"node:{node_id}:uses", cast(list[str], nb_w.get("uses", [])),
                                    cast(list[str], no_w["uses"]), cast(list[str], nt_w["uses"]))
        if "members" in no_w or "members" in nt_w:
            mb = cast(Wire, nb_w.get("members", {}))
            mo, mt = cast(Wire, no_w.get("members", {})), cast(Wire, nt_w.get("members", {}))
            members: Wire = {}
            for slot in _SLOTS:
                key = f"node:{node_id}:members.{slot}"
                if slot in _LISTS:
                    members[slot] = m.sequence(key, cast(list[str], mb.get(slot, [])),
                                               cast(list[str], mo.get(slot, [])), cast(list[str], mt.get(slot, [])))
                else:
                    value = m.value(key, mb.get(slot, ABSENT), mo.get(slot, ABSENT), mt.get(slot, ABSENT))
                    if value is not ABSENT:
                        members[slot] = value
            merged["members"] = members
        nodes.append(merged)
    result["nodes"] = nodes
    roots = m.keyed("roots", cast(list[Wire], b["roots"]), cast(list[Wire], o["roots"]),
                    cast(list[Wire], t["roots"]), "node_id")
    result["roots"] = roots
    result["publications"] = m.keyed("publication", cast(list[Wire], b["publications"]),
        cast(list[Wire], o["publications"]), cast(list[Wire], t["publications"]), "publication_id")
    pb, po, pt = (cast(Wire, x["presentations"]) for x in (b, o, t))
    catalogs: list[Wire] = []
    cb = {cast(str, c["locale"]): c for c in cast(list[Wire], pb["catalogs"])}
    co = {cast(str, c["locale"]): c for c in cast(list[Wire], po["catalogs"])}
    ct = {cast(str, c["locale"]): c for c in cast(list[Wire], pt["catalogs"])}
    order = m.sequence("presentations.locales", list(cb), list(co), list(ct))
    for locale in order:
        entries = m.keyed(f"presentation:{locale}", cast(list[Wire], cb.get(locale, {}).get("entries", [])),
            cast(list[Wire], co.get(locale, {}).get("entries", [])),
            cast(list[Wire], ct.get(locale, {}).get("entries", [])), "node_id")
        catalogs.append({"locale": locale, "entries": entries})
    result["presentations"] = {"default_locale": m.value("presentations.default_locale",
        pb["default_locale"], po["default_locale"], pt["default_locale"]), "catalogs": catalogs}
    unused = set(m.resolutions) - m.used
    if unused:
        raise GraphError("schema_invalid", "Resolutions name no conflict: " + ", ".join(sorted(unused)))
    if m.conflicts:
        return MergeResult(None, tuple(m.conflicts))
    try:
        candidate = GraphDefinition.from_json(result, verify_contracts=False)
    except GraphError as error:
        return MergeResult(None, (MergeConflict("graph", "structure", ABSENT, ABSENT, ABSENT,
                                                f"{error.code}: {error}"),))
    return MergeResult(candidate, ())
