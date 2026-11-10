"""A graph as a plain directory of Markdown and JSON files (no I/O, no business terms).

``contexture.directory/v1`` encodes selected nodes of a definition as a mapping
of relative POSIX paths to bytes, and overlays such a mapping back onto a
definition to produce a complete candidate. Writing the files to disk, zipping
them, and committing the candidate are the caller's job; committing must go
through an ordinary transaction (``ManagedGraphHost.import_directory`` uses the
existing reconcile path), so protection, admission and CAS all still apply.

Layout::

    .contexture/directory.json      schema, graph_id, revision, exported ids, placeholders
    <root name>/ROLE.md             front matter + instructions
    <role>/roles/<child>/ROLE.md    child Roles (``slot`` in front matter for process slots)
    <role>/skills/<name>/SKILL.md   Skills (Agent Skill compatible front matter)
    <role>/tools/<name>.json        Tool cards (the node wire; bindings by reference only)

Front matter is the block between two ``---`` lines; every line is
``key: value`` where value is a JSON value or plain one-line text, a strict
subset of YAML. Names are percent-escaped where a file system would reject
them. A node file with an ``id`` updates that node; one without creates a node;
an exported id missing from the directory is deleted. Renaming a folder renames
the node unless the front matter states ``name``. Ancestors outside the export
are bare folders recorded as ``placeholders``; their content is never written.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from uuid import uuid4

from .canonical import thaw_json, validate_id
from .errors import GraphError
from .index import GraphIndex
from .model import GraphDefinition, InstructionTemplate, NodeSpec, Slot, TextPart

DIRECTORY_SCHEMA = "contexture.directory/v1"
MANIFEST = ".contexture/directory.json"
ROLE_FILE = "ROLE.md"
SKILL_FILE = "SKILL.md"
_ROLES, _SKILLS, _TOOLS = "roles", "skills", "tools"
_REF = re.compile(r"\{\{node:([^|{}\s]+)(?:\|([^{}]*))?\}\}")
_UNSAFE = set('%\\:*?"<>|')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}
Files = Mapping[str, bytes]


def _fail(message: str, path: str | None = None, code: str = "schema_invalid") -> GraphError:
    return GraphError(code, _bounded(message if path is None else f"{message}: {path}"))


def _bounded(text: str) -> str:
    return text if len(text) <= 1024 else text[:1021] + "..."


# -- names ------------------------------------------------------------------


def escape_name(name: str) -> str:
    """A file-system-safe, reversible folder or file name for a node name."""
    chars = [f"%{ord(c):02X}" if c in _UNSAFE else c for c in name]
    if chars and chars[0] in (".", " "):
        chars[0] = f"%{ord(chars[0]):02X}"
    if chars and chars[-1] in (".", " "):
        chars[-1] = f"%{ord(chars[-1]):02X}"
    if name.split(".")[0].lower() in _RESERVED:
        chars[0] = f"%{ord(name[0]):02X}"
    return "".join(chars)


def unescape_name(text: str) -> str:
    return re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), text)


# -- front matter ------------------------------------------------------------


def _dump_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))


def render_front_matter(fields: Mapping[str, object], body: str) -> bytes:
    lines = ["---", *(f"{key}: {_dump_value(value)}" for key, value in fields.items()), "---"]
    # One newline always follows the body; the parser removes exactly one.
    return ("\n".join(lines) + "\n" + body + "\n").encode("utf-8")


def parse_front_matter(raw: bytes, path: str) -> tuple[dict[str, object], str]:
    """Front matter fields and the body (CRLF normalized, one final newline removed)."""
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise _fail("File is not UTF-8 text", path) from None
    text = text.removeprefix("\ufeff")
    fields: dict[str, object] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---", 3)
        while end != -1 and text[end + 4:end + 5] not in ("", "\n"):
            end = text.find("\n---", end + 4)
        if end == -1:
            raise _fail("Unterminated front matter", path)
        for number, line in enumerate(text[4:end].split("\n"), 2):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, sep, value = line.partition(":")
            if not sep or not key.strip() or key != key.strip():
                raise _fail(f"Front matter line {number} must be 'key: value'", path)
            if key in fields:
                raise _fail(f"Duplicate front matter key {key!r}", path)
            value = value.strip()
            try:
                fields[key] = json.loads(value)
            except ValueError:
                fields[key] = value
        body = text[end + 5:]
    return fields, body.removesuffix("\n")


def _string(fields: Mapping[str, object], key: str, path: str) -> str | None:
    """A string field; plain YAML-like scalars that JSON read as numbers stay text."""
    if key not in fields:
        return None
    value = fields[key]
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)) or value is None:
        return _dump_value(value)
    raise _fail(f"Front matter {key!r} must be text", path)


# -- instruction bodies -------------------------------------------------------


def _body_of(spec: NodeSpec) -> tuple[str, dict[str, object]]:
    """The Markdown body, or ("", fields) when only JSON keeps it lossless."""
    if spec.instructions is not None:
        text = spec.instructions
        if "\r" in text or _REF.search(text) or text.startswith("\ufeff"):
            return "", {"instructions": text}
        return text, {}
    template = spec.instruction_template
    assert template is not None
    pieces: list[str] = []
    for part in template.parts:
        if isinstance(part, TextPart):
            pieces.append(part.text)
        elif part.label is None:
            pieces.append(f"{{{{node:{part.target_id}}}}}")
        else:
            pieces.append(f"{{{{node:{part.target_id}|{part.label}}}}}")
    body = "".join(pieces)
    # Keep the readable form only when it parses back to the identical template.
    reparsed = (None if "\r" in body or body.startswith("\ufeff")
                else _template_of(body, force=True))
    if reparsed is None or reparsed.as_json() != template.as_json():
        return "", {"instruction_template": template.as_json()}
    return body, {"template": True}


def _template_of(body: str, *, force: bool = False) -> InstructionTemplate | None:
    """Parse ``{{node:...}}`` references; None for plain text unless ``force``."""
    parts: list[dict[str, object]] = []
    position = 0
    for match in _REF.finditer(body):
        if match.start() > position:
            parts.append({"kind": "Text", "text": body[position:match.start()]})
        ref: dict[str, object] = {"kind": "NodeRef", "target_id": match.group(1)}
        if match.group(2) is not None:
            ref["label"] = match.group(2)
        parts.append(ref)
        position = match.end()
    if not parts and not force:
        return None
    if position < len(body):
        parts.append({"kind": "Text", "text": body[position:]})
    return InstructionTemplate.from_json({"parts": parts})


# -- selection ----------------------------------------------------------------


def _presentations_of(definition: GraphDefinition, node_id: str) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for catalog in definition.presentations.catalogs:
        for entry in catalog.entries:
            if entry.node_id == node_id:
                value = entry.as_json()
                value.pop("node_id")
                result[catalog.locale] = value
    return result


def select_nodes(definition: GraphDefinition, *, mutable_only: bool = False,
                 changed_since: GraphDefinition | None = None) -> tuple[str, ...]:
    """Node ids in containment preorder, filtered by two business-neutral rules.

    ``mutable_only`` keeps effectively mutable nodes. ``changed_since`` keeps
    nodes that are new or whose own definition or presentations differ from
    that earlier definition (e.g. the product's seed: what an author added on
    top). A parent that only gained or lost members is not selected: members
    carry their own presence, and their parent's content stays unexported.
    """
    index = GraphIndex.build(definition, verify_contracts=False)
    result: list[str] = []
    for node_id in index.preorder:
        if mutable_only and not index.locations[node_id].effective_mutable:
            continue
        if changed_since is not None:
            before = changed_since.nodes.get(node_id)
            if (before is not None and _own(before) == _own(definition.nodes[node_id])
                    and _presentations_of(changed_since, node_id)
                    == _presentations_of(definition, node_id)):
                continue
        result.append(node_id)
    return tuple(result)


def _own(spec: NodeSpec) -> dict[str, object]:
    """A node's wire without its member list."""
    wire = spec.as_json()
    wire.pop("members", None)
    return wire


# -- encode -------------------------------------------------------------------


def _segment(index: GraphIndex, definition: GraphDefinition, node_id: str,
             names: Mapping[str, str]) -> str:
    slot = index.locations[node_id].slot
    name = names[node_id]
    if slot is None:
        return f"{name}.json" if definition.nodes[node_id].kind == "tool" else name
    if slot == "skills":
        return f"{_SKILLS}/{name}"
    if slot == "tools":
        return f"{_TOOLS}/{name}.json"
    return f"{_ROLES}/{name}"


def _sibling_names(definition: GraphDefinition, index: GraphIndex) -> dict[str, str]:
    """Escaped names, unique per parent folder even on case-insensitive systems."""
    groups: dict[tuple[str | None, str], list[str]] = {}
    for node_id in index.preorder:
        loc = index.locations[node_id]
        group = {"skills": _SKILLS, "tools": _TOOLS, None: ""}.get(loc.slot, _ROLES)
        groups.setdefault((loc.parent_id, group), []).append(node_id)
    names: dict[str, str] = {}
    for members in groups.values():
        seen: set[str] = set()
        for node_id in members:
            base = escape_name(definition.nodes[node_id].name)
            candidate, n = base, 1
            while candidate.casefold() in seen:
                n += 1
                candidate = f"{base}~{n}"
            seen.add(candidate.casefold())
            names[node_id] = candidate
    return names


def encode_directory(definition: GraphDefinition, *, node_ids: Sequence[str] | None = None,
                     revision: str | None = None) -> dict[str, bytes]:
    """Relative path -> bytes for the selected nodes (whole graph by default)."""
    index = GraphIndex.build(definition, verify_contracts=False)
    selected = list(index.preorder) if node_ids is None else [validate_id(n) for n in node_ids]
    chosen = set(selected)
    if len(chosen) != len(selected):
        raise _fail("Duplicate selected node")
    for node_id in selected:
        index.get(node_id)
    names = _sibling_names(definition, index)

    def folder(node_id: str) -> str:
        chain: list[str] = []
        current: str | None = node_id
        while current is not None:
            chain.append(_segment(index, definition, current, names))
            current = index.locations[current].parent_id
        return "/".join(reversed(chain))

    files: dict[str, bytes] = {}
    placeholders: dict[str, str] = {}
    for node_id in selected:
        spec = definition.nodes[node_id]
        loc = index.locations[node_id]
        parent = loc.parent_id
        while parent is not None and parent not in chosen:
            placeholders[folder(parent)] = parent
            parent = index.locations[parent].parent_id
        path = folder(node_id)
        presentations = _presentations_of(definition, node_id)
        if spec.kind == "tool":
            wire = spec.as_json()
            if presentations:
                wire["presentations"] = presentations
            files[path] = (json.dumps(wire, ensure_ascii=False, sort_keys=True, indent=2)
                           + "\n").encode("utf-8")
            continue
        fields: dict[str, object] = {"id": node_id}
        if spec.kind == "skill" or unescape_name(names[node_id]) != spec.name:
            fields["name"] = spec.name
        fields["description"] = spec.description
        if loc.slot in ("pre_process", "post_process"):
            fields["slot"] = loc.slot
        if spec.role_variant not in (None, "ordinary"):
            fields["variant"] = spec.role_variant
        if loc.slot is None:
            fields["plane"] = loc.plane
        if spec.mutable != "inherit":
            fields["mutable"] = spec.mutable
        if spec.uses:
            fields["uses"] = list(spec.uses)
        if spec.members is not None:
            for slot, group in (("children", _ROLES), ("skills", _SKILLS), ("tools", _TOOLS)):
                members = cast(tuple[str, ...], getattr(spec.members, slot))
                if len(members) > 1:
                    fields[f"order_{group}"] = [unescape_name(names[m]) for m in members]
        if spec.extensions:
            fields["extensions"] = thaw_json(spec.extensions)
        if presentations:
            fields["presentations"] = presentations
        body, extra = _body_of(spec)
        fields.update(extra)
        files[f"{path}/{SKILL_FILE if spec.kind == 'skill' else ROLE_FILE}"] = render_front_matter(fields, body)
    manifest = {"schema": DIRECTORY_SCHEMA, "graph_id": definition.graph_id, "revision": revision,
                "definition_digest": definition.digest(), "nodes": selected,
                "placeholders": dict(sorted(placeholders.items()))}
    files[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return dict(sorted(files.items()))


# -- decode and overlay ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Entry:
    path: str          # folder (role/skill) or file (tool) path
    parent: str        # parent folder path ("" for a root)
    group: str         # "" root, roles, skills, tools
    folder_name: str
    kind: str
    fields: Mapping[str, object]
    body: str
    tool: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class DirectoryOverlay:
    """A complete candidate plus what the directory changed, by node id."""

    candidate: GraphDefinition
    created: tuple[str, ...]
    updated: tuple[str, ...]
    deleted: tuple[str, ...]
    ignored: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.deleted)

    def as_json(self) -> dict[str, object]:
        return {"created": list(self.created), "updated": list(self.updated),
                "deleted": list(self.deleted), "ignored": list(self.ignored)}


def read_manifest(files: Files) -> dict[str, object]:
    raw = files.get(MANIFEST)
    if raw is None:
        raise _fail("Directory manifest missing", MANIFEST)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _fail("Directory manifest is not JSON", MANIFEST) from None
    if not isinstance(value, dict) or value.get("schema") != DIRECTORY_SCHEMA:
        raise _fail("Unsupported directory schema", MANIFEST)
    nodes, placeholders = value.get("nodes"), value.get("placeholders", {})
    if not isinstance(nodes, list) or not isinstance(placeholders, dict):
        raise _fail("Directory manifest needs nodes and placeholders", MANIFEST)
    return value


def _entries(files: Files) -> tuple[list[_Entry], list[str]]:
    entries: list[_Entry] = []
    ignored: list[str] = []
    for path in sorted(files):
        if path == MANIFEST:
            continue
        parts = path.split("/")
        if any(p in ("", ".", "..") for p in parts) or path.startswith("/"):
            raise _fail("Unsafe path", path)
        leaf = parts[-1]
        root = len(parts) == 2
        if leaf == ROLE_FILE and (root or (len(parts) >= 4 and parts[-3] == _ROLES)):
            folder, group, kind = "/".join(parts[:-1]), "" if root else _ROLES, "role"
        elif leaf == SKILL_FILE and (root or (len(parts) >= 4 and parts[-3] == _SKILLS)):
            folder, group, kind = "/".join(parts[:-1]), "" if root else _SKILLS, "skill"
        elif leaf.endswith(".json") and (len(parts) == 1 or (len(parts) >= 3 and parts[-2] == _TOOLS)):
            folder, group, kind = path, "" if len(parts) == 1 else _TOOLS, "tool"
        else:
            ignored.append(path)
            continue
        segments = folder.split("/")
        parent = "/".join(segments[:-2]) if group else ""
        name = segments[-1].removesuffix(".json") if kind == "tool" else segments[-1]
        if kind == "tool":
            try:
                tool = json.loads(files[path].decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise _fail("Tool card is not JSON", path) from None
            if not isinstance(tool, dict):
                raise _fail("Tool card must be a JSON object", path)
            entries.append(_Entry(folder, parent, group, name, kind, {}, "", tool))
        else:
            fields, body = parse_front_matter(files[path], path)
            entries.append(_Entry(folder, parent, group, name, kind, fields, body, None))
    return entries, ignored


def _node_wire(entry: _Entry, node_id: str, existing: NodeSpec | None) -> dict[str, object]:
    path = entry.path
    if entry.tool is not None:
        wire = {k: v for k, v in entry.tool.items() if k != "presentations"}
        wire["node_id"] = node_id
        wire.setdefault("name", unescape_name(entry.folder_name))
        return wire
    fields = entry.fields
    known = {"id", "name", "description", "slot", "variant", "plane", "mutable", "uses",
             "order_roles", "order_skills", "order_tools", "extensions", "presentations",
             "instructions", "instruction_template", "template"}
    unknown = sorted(set(fields) - known)
    if unknown:
        raise _fail(f"Unknown front matter keys {unknown}", path)
    wire: dict[str, object] = dict(existing.as_json()) if existing is not None else {}
    wire.update({"node_id": node_id, "kind": entry.kind})
    wire["name"] = _string(fields, "name", path) or unescape_name(entry.folder_name)
    description = _string(fields, "description", path)
    if description is not None:
        wire["description"] = description
    elif existing is None:
        raise _fail("A new node needs a description", path)
    for key in ("instructions", "instruction_template"):
        wire.pop(key, None)
    stored = [key for key in ("instructions", "instruction_template") if key in fields]
    if len(stored) > 1 or (stored and entry.body.strip()):
        # Never ignore an edit silently: one representation per file.
        raise _fail(f"Instructions are in both the body and front matter {stored}; keep one", path)
    if "instructions" in fields:
        wire["instructions"] = _string(fields, "instructions", path) or ""
    elif "instruction_template" in fields:
        wire["instruction_template"] = fields["instruction_template"]
    else:
        template = _template_of(entry.body, force=fields.get("template") is True)
        if template is None:
            wire["instructions"] = entry.body
        else:
            wire["instruction_template"] = template.as_json()
    wire["mutable"] = fields.get("mutable", existing.mutable if existing is not None else "inherit")
    wire["uses"] = fields.get("uses", [])
    wire["extensions"] = fields.get("extensions", {})
    if entry.kind == "role":
        wire["role_variant"] = fields.get("variant", "ordinary")
        wire.setdefault("members", {"children": [], "skills": [], "tools": []})
    return wire


def _ordered(current: Sequence[str], wanted: Sequence[str], keep: set[str]) -> list[str]:
    """Members outside the directory keep their positions; directory members fill
    the positions they already held, in the requested order; extra ones (new or
    moved in) follow at the end."""
    queue = list(wanted)
    targets = set(wanted)
    result: list[str] = []
    for member in current:
        if member in keep:
            result.append(member)
        elif member in targets and queue:
            result.append(queue.pop(0))
    return result + queue


def overlay_directory(definition: GraphDefinition, files: Files, *,
                      id_factory: Callable[[], str] | None = None) -> DirectoryOverlay:
    """The complete candidate after applying an edited directory to ``definition``."""
    manifest = read_manifest(files)
    if manifest.get("graph_id") != definition.graph_id:
        raise _fail("Directory belongs to another graph", MANIFEST)
    exported = {validate_id(n) for n in cast(list[object], manifest["nodes"])}
    placeholders = {str(k): str(v) for k, v in cast(dict[str, object], manifest["placeholders"]).items()}
    make_id = id_factory or (lambda: f"n-{uuid4().hex}")
    index = GraphIndex.build(definition, verify_contracts=False)
    entries, ignored = _entries(files)

    ids: dict[str, str] = {}
    seen: set[str] = set()
    for entry in entries:
        given = entry.tool.get("node_id") if entry.tool is not None else entry.fields.get("id")
        node_id = make_id() if given is None else given
        if not isinstance(node_id, str):
            raise _fail("Node id must be text", entry.path)
        try:
            validate_id(node_id)
        except GraphError:
            raise _fail("Invalid node id", entry.path) from None
        if node_id in seen:
            raise _fail("Node id appears twice", entry.path)
        seen.add(node_id)
        existing = definition.nodes.get(node_id)
        if existing is not None and existing.kind != entry.kind:
            raise _fail("A file cannot change a node's kind", entry.path)
        ids[entry.path] = node_id
    folders = {entry.path: ids[entry.path] for entry in entries if entry.kind == "role"}
    for path, node_id in placeholders.items():
        if node_id not in definition.nodes:
            raise _fail("Placeholder refers to an unknown node", path)
        folders.setdefault(path, node_id)
    inside = set(ids.values())

    # Placement requested by the directory: parent id, slot, plane for roots.
    placement: dict[str, tuple[str | None, Slot]] = {}
    for entry in entries:
        node_id = ids[entry.path]
        if not entry.parent:
            placement[node_id] = (None, "children")
            continue
        parent_id = folders.get(entry.parent)
        if parent_id is None:
            raise _fail("Folder has no ROLE.md and is not a placeholder", entry.parent)
        slot = cast(Slot, {_SKILLS: "skills", _TOOLS: "tools"}.get(entry.group)
                    or entry.fields.get("slot", "children"))
        if slot not in ("children", "pre_process", "post_process", "skills", "tools"):
            raise _fail("Unknown slot", entry.path)
        placement[node_id] = (parent_id, slot)

    deleted: set[str] = set()
    for node_id in exported - inside:
        if node_id in definition.nodes:
            for member in index.preorder[index.locations[node_id].preorder:
                                         index.locations[node_id].subtree_end]:
                if member not in inside:
                    deleted.add(member)

    changed: dict[str, Mapping[str, object] | None] = {n: None for n in deleted}
    wires: dict[str, dict[str, object]] = {}
    for entry in entries:
        node_id = ids[entry.path]
        wires[node_id] = _node_wire(entry, node_id, definition.nodes.get(node_id))

    # Rebuild member lists of every Role that gains, loses or reorders members.
    order_of: dict[str, dict[str, list[str]]] = {}
    for entry in entries:
        if entry.kind == "role":
            order_of[ids[entry.path]] = {
                group: [str(n) for n in cast(list[object], entry.fields.get(f"order_{group}", []))]
                for group in (_ROLES, _SKILLS, _TOOLS)}
    names = {ids[e.path]: str(wires[ids[e.path]]["name"]) for e in entries}
    by_parent: dict[str, dict[Slot, list[str]]] = {}
    for node_id, (parent_id, slot) in placement.items():
        if parent_id is not None:
            by_parent.setdefault(parent_id, {}).setdefault(slot, []).append(node_id)
    moved_out = {n for n in inside if n in index.locations
                 and index.locations[n].parent_id != placement[n][0]}
    touched = set(by_parent) | {index.locations[n].parent_id for n in deleted | moved_out
                                if n in index.locations} - {None}
    for parent_id in sorted(cast(set[str], touched)):
        if parent_id in deleted:
            continue
        wire = wires.get(parent_id) or dict(definition.nodes[parent_id].as_json())
        current = cast(dict[str, object], wire.get("members") or {})
        requested = by_parent.get(parent_id, {})
        order = order_of.get(parent_id, {})
        members: dict[str, object] = {}
        for slot, group in (("children", _ROLES), ("skills", _SKILLS), ("tools", _TOOLS)):
            held = [str(m) for m in cast(list[object], current.get(slot, []))]
            keep = {m for m in held if m not in inside and m not in deleted}
            rank = {name: i for i, name in enumerate(order.get(group, []))}
            position = {m: i for i, m in enumerate(held)}
            wanted = sorted(requested.get(cast(Slot, slot), []),
                            key=lambda n: (rank.get(names[n], len(rank)), position.get(n, len(held))))
            members[slot] = _ordered(held, wanted, keep)
        for slot in ("pre_process", "post_process"):
            single = current.get(slot)
            wanted = requested.get(cast(Slot, slot), [])
            if len(wanted) > 1:
                raise _fail(f"Only one {slot} member allowed", parent_id)
            if wanted:
                members[slot] = wanted[0]
            elif single is not None and single not in inside and single not in deleted:
                members[slot] = single
        wire["members"] = members
        wires[parent_id] = wire

    header = definition.header()
    roots = [r for r in cast(list[dict[str, object]], header["roots"])
             if r["node_id"] not in deleted and placement.get(str(r["node_id"]), (None,))[0] is None]
    rooted = {str(r["node_id"]) for r in roots}
    for entry in entries:
        node_id = ids[entry.path]
        if placement[node_id][0] is None and node_id not in rooted:
            plane = entry.fields.get("plane", "model")
            roots.append({"node_id": node_id, "plane": plane})
    header["roots"] = roots

    presentations = cast(dict[str, object], header["presentations"])
    catalogs = cast(list[dict[str, object]], presentations["catalogs"])
    wanted_presentations: dict[str, Mapping[str, object]] = {}
    for entry in entries:
        source = entry.tool if entry.tool is not None else entry.fields
        value = source.get("presentations", {})
        if not isinstance(value, Mapping):
            raise _fail("presentations must map locale to entry", entry.path)
        wanted_presentations[ids[entry.path]] = cast(Mapping[str, object], value)
    governed = inside | deleted
    for catalog in catalogs:
        entries_json = [e for e in cast(list[dict[str, object]], catalog["entries"])
                        if e["node_id"] not in governed]
        for node_id in sorted(inside):
            item = wanted_presentations.get(node_id, {}).get(str(catalog["locale"]))
            if item is not None:
                entries_json.append({**cast(Mapping[str, object], item), "node_id": node_id})
        catalog["entries"] = entries_json
    known_locales = {str(c["locale"]).lower() for c in catalogs}
    for node_id in sorted(inside):
        for locale, item in wanted_presentations.get(node_id, {}).items():
            if locale.lower() not in known_locales:
                catalogs.append({"locale": locale, "entries": [
                    {**cast(Mapping[str, object], item), "node_id": node_id}]})
                known_locales.add(locale.lower())

    changed.update(wires)
    try:
        candidate = GraphDefinition.assemble(header, {k: v for k, v in definition.nodes.items()},
                                             changed, verify_contracts=False)
    except GraphError as error:
        raise GraphError(error.code, _bounded(f"Directory does not form a valid graph: {error}"),
                         location=error.location, diagnostics=error.diagnostics) from None
    created = sorted(n for n in inside if n not in definition.nodes)
    updated = sorted(n for n in candidate.nodes if n in definition.nodes
                     and (not candidate.nodes[n].same(definition.nodes[n])
                          or _presentations_of(candidate, n) != _presentations_of(definition, n)))
    return DirectoryOverlay(candidate, tuple(created), tuple(updated), tuple(sorted(deleted)),
                            tuple(ignored))


def directory_paths(files: Iterable[str]) -> list[str]:
    """Sorted unique paths, rejecting absolute or traversing names (for adapters)."""
    result: set[str] = set()
    for path in files:
        clean = path.replace("\\", "/")
        if clean.startswith("/") or any(p in ("", ".", "..") for p in clean.split("/")):
            raise _fail("Unsafe path", path)
        result.add(clean)
    return sorted(result)
