"""Bounded WP09/WP10 internal disclosure, not a managed wire/profile adapter.

An application supplies a pinned plan, generation and freshly authorized stable
IDs with current-view evidence on EVERY request. Evidence is an opaque app-owned
identifier, not a credential checked here. This pure renderer cannot establish
identity, leases, freshness, or policy. Never reuse it across authority changes.
No execution, paging, Catalog envelope, or G1 migration claim is made here.
Literal instructions remain unresolved inventory, byte-exact and unrewritten.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import json
from typing import Any, Literal

from .canonical import thaw_json, validate_id
from .compiler import StructuralPlan
from .errors import GraphError
from .model import TextPart
from .presentation_format import candidate_process_instruction
from .references import format_reference, parse_reference


def _denied() -> GraphError:
    # Never include an identity, path, hidden count, or global graph diagnostic.
    return GraphError("permission_denied", "Node is unavailable in this disclosure view")


@dataclass(slots=True)
class _Budget:
    limit: int
    count: int = 0

    def card(self) -> None:
        self.count += 1
        if self.count > self.limit:
            raise GraphError("too_large", "Disclosure exceeds card limit; paging is required")


@dataclass(frozen=True, slots=True, kw_only=True)
class ManagedDisclosure:
    """Immutable request-local projection of a compiler-owned StructuralPlan.

    authorized_ids is the application's already intersected initial/current
    stable-ID ceiling; unknown/deleted IDs cannot restore anything in this plan.
    selection_anchors additionally narrows it to selected subtrees. None means
    no extra selection; an empty set means no visibility. Routing visibility is
    separate from model opening: a person-reserved model-plane target keeps its
    ordinary static route card (no invented ``openable`` field).

    Returned dictionaries are newly materialized JSON, with no mutable aliases
    to the plan or other responses. No hidden metadata/digests/counts are emitted.
    Limits refuse the COMPLETE result rather than silently truncating Role.open.
    """

    plan: StructuralPlan = field(repr=False)
    generation_uuid: str
    authorized_ids: frozenset[str] = field(repr=False)
    current_view_evidence: str = field(repr=False)
    selection_anchors: frozenset[str] | None = field(default=None, repr=False)
    audience: Literal["model", "person"] = "model"
    max_cards: int = 1000
    max_response_bytes: int = 8 * 1024 * 1024
    max_inspect_refs: int = 32
    _visible: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.plan, StructuralPlan):
            raise GraphError("schema_invalid", "Expected a compiled StructuralPlan")
        if (type(self.current_view_evidence) is not str
                or not self.current_view_evidence.strip()
                or self.audience not in ("model", "person")):
            raise GraphError("schema_invalid", "Explicit current-view evidence and audience required")
        for limit in (self.max_cards, self.max_response_bytes, self.max_inspect_refs):
            if type(limit) is not int or limit < 1:
                raise GraphError("schema_invalid", "Disclosure limits must be positive integers")
        index = self.plan.index
        # Validate even empty ceilings; generations are never minted here.
        format_reference(self.plan.definition.graph_id, self.generation_uuid, index.preorder[0])
        if not isinstance(self.authorized_ids, (set, frozenset)):
            raise GraphError("schema_invalid", "Explicit stable-ID set required")
        authorized = frozenset(validate_id(n) for n in self.authorized_ids)
        object.__setattr__(self, "authorized_ids", authorized)
        selected: set[str] | None = None
        if self.selection_anchors is not None:
            if not isinstance(self.selection_anchors, (set, frozenset)):
                raise GraphError("schema_invalid", "Selection must be a stable-ID anchor set")
            anchors = frozenset(validate_id(n) for n in self.selection_anchors)
            object.__setattr__(self, "selection_anchors", anchors)
            if not anchors <= index.locations.keys():
                raise _denied()
            selected = {node.node_id for n in anchors for node in index.subtree(n)}
        visible = frozenset(n for n in index.preorder if n in authorized
            and (selected is None or n in selected)
            and (self.audience == "person" or index.locations[n].plane == "model"))
        object.__setattr__(self, "_visible", visible)

    def _ref(self, node_id: str) -> str:
        return format_reference(self.plan.definition.graph_id, self.generation_uuid, node_id)

    def _resolve(self, ref: str, *, active: bool = True) -> str:
        parsed = parse_reference(ref)
        if (parsed.graph_id != self.plan.definition.graph_id
                or parsed.generation_uuid != self.generation_uuid):
            raise GraphError("stale_reference", "Reference does not match the pinned generation")
        if parsed.node_id not in self._visible:
            raise _denied()
        if active and self.audience == "model" and not self.plan.index.model_may_open(parsed.node_id):
            raise _denied()
        return parsed.node_id

    def _route(self, node_id: str, budget: _Budget) -> dict[str, Any]:
        if node_id not in self._visible:
            raise _denied()
        budget.card()
        node = self.plan.index.get(node_id)
        presentation = self.plan.presentations[node_id]
        result: dict[str, Any] = {"kind": node.kind, "name": node.name}
        # Static presentation adds title only where a catalog entry exists.
        if self.plan.index.presentation(node_id, self.plan.selected_locale) is not None:
            result["title"] = presentation.title
        result.update(description=presentation.description, ref=self._ref(node_id))
        return result

    def _uses(self, node_id: str, budget: _Budget) -> list[dict[str, Any]]:
        return [self._route(n, budget) for n in self.plan.index.get(node_id).uses
                if n in self._visible]

    def _tool(self, node_id: str, budget: _Budget) -> dict[str, Any]:
        payload = self._route(node_id, budget)
        if self.plan.definition.mode == "runtime":
            entry = self.plan.bindings[node_id].entry
            payload.update(read_only=entry.effect == "read_only",
                           input_schema=thaw_json(entry.input_schema))
        if self.plan.index.get(node_id).uses:
            payload["uses"] = self._uses(node_id, budget)
        return payload

    def _members(self, node_id: str, budget: _Budget, *, active: bool) -> dict[str, Any]:
        grouped: dict[str, Any] = {"roles": [], "skills": [], "tools": []}
        members = self.plan.index.get(node_id).members
        for _, _, n in members.placements() if members is not None else ():
            if n not in self._visible:
                continue
            kind = self.plan.index.get(n).kind
            card = self._tool(n, budget) if active and kind == "tool" else self._route(n, budget)
            grouped[kind + "s"].append(card)
        return grouped

    def _instructions(self, node_id: str) -> str:
        """Local typed-material rendering, NOT an accepted escaping protocol.

        Keep Text byte-exact. Emit only repr(canonical ASCII ref) for NodeRef;
        omit optional human labels entirely, rather than treating hostile labels
        as Markdown/code. Labels remain separate in the source plan. No fallback
        to author paths or literal substitution is possible for typed targets.
        """
        parts: list[str] = []
        for part in self.plan.instructions[node_id]:
            if isinstance(part, TextPart):
                parts.append(part.text)
            else:
                if part.target_id not in self._visible:
                    raise _denied()
                if (self.audience == "model"
                        and not self.plan.index.model_may_open(part.target_id)):
                    raise _denied()
                parts.append(repr(self._ref(part.target_id)))
        return "".join(parts)

    def _processes(self, node_id: str, payload: dict[str, Any]) -> None:
        process = self.plan.processes[node_id]
        for kind, target in (("PreProcess", process.pre_process),
                             ("PostProcess", process.post_process)):
            if target is None:
                continue
            if (target not in self._visible
                    or (self.audience == "model" and not self.plan.index.model_may_open(target))):
                raise _denied()
            ref = self._ref(target)
            if not any(card["ref"] == ref for card in payload["roles"]):
                raise _denied()
            if kind == "PreProcess":
                contract = candidate_process_instruction("PreProcess", ref)
                payload["pre_process"] = ref
                payload["instructions"] = contract + "\n\n" + payload["instructions"]
            else:
                contract = candidate_process_instruction("PostProcess", ref)
                payload["post_process"] = ref
                payload["instructions"] += "\n\n" + contract

    def _finish(self, payload: dict[str, Any]) -> dict[str, Any]:
        size = 0
        encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        for chunk in encoder.iterencode(payload):
            size += len(chunk.encode("utf-8"))
            if size > self.max_response_bytes:
                raise GraphError("too_large", "Disclosure exceeds response byte limit; paging is required")
        return payload

    def discover(self) -> dict[str, Any]:
        """Only projected roots, in forest preorder; never a descendant roster."""
        budget = _Budget(self.max_cards)
        grouped: dict[str, Any] = {"roles": [], "skills": [], "tools": []}
        for n in self.plan.index.preorder:
            if n in self._visible and self.plan.index.locations[n].parent_id not in self._visible:
                grouped[self.plan.index.get(n).kind + "s"].append(self._route(n, budget))
        return self._finish(grouped)

    def route(self, ref: str) -> dict[str, Any]:
        """Routing visibility does not grant model opening of reserved targets."""
        return self._finish(self._route(self._resolve(ref, active=False), _Budget(self.max_cards)))

    def inspect(self, refs: Sequence[str]) -> dict[str, Any]:
        if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
            raise GraphError("schema_invalid", "Inspect requires an ordered reference sequence")
        if not 1 <= len(refs) <= self.max_inspect_refs:
            raise GraphError("schema_invalid", "Inspect reference count is outside its limit")
        # Prevalidate the ENTIRE batch (including visibility) before rendering.
        nodes = [self._resolve(ref) for ref in refs]
        if len(set(nodes)) != len(nodes):
            raise GraphError("schema_invalid", "Inspect references must be unique")
        budget = _Budget(self.max_cards)
        return self._finish({"notice": (
            "These are routing cards for candidate evaluation. No Role or Skill "
            "has been activated, no instructions or execution facets are disclosed, "
            "and no Tool has been invoked."), "items": [
                {"node": self._route(n, budget),
                 "members": self._members(n, budget, active=False),
                 "uses": self._uses(n, budget)} for n in nodes]})

    def open(self, ref: str) -> dict[str, Any]:
        n = self._resolve(ref)
        budget = _Budget(self.max_cards)
        kind = self.plan.index.get(n).kind
        if kind == "tool":
            return self._finish(self._tool(n, budget))
        payload = self._route(n, budget)
        payload["instructions"] = self._instructions(n)
        if kind == "role":
            payload.update(self._members(n, budget, active=True))
        if self.plan.index.get(n).uses:
            payload["uses"] = self._uses(n, budget)
        if kind == "role":
            self._processes(n, payload)
        return self._finish(payload)