"""Immutable managed authoring values; no handlers, transport or persistence.

Construct values with ``from_json``. Constructors deliberately reject partial
instances. Runtime publication evidence comes from a pinned registry, never
from an author's input_contract assertion. This is not compiler lowering.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import hashlib
from dataclasses import dataclass, field
from ipaddress import IPv6Address
import re
from types import MappingProxyType
from typing import Literal, TypeVar, cast
from unicodedata import normalize

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .canonical import (
    FrozenJSON, canonical_digest, canonical_json, decode_json, freeze_json, thaw_json, validate_id,
)
from .errors import GraphError

Kind = Literal["role", "skill", "tool"]
Plane = Literal["model", "prompt"]
Slot = Literal["pre_process", "children", "post_process", "skills", "tools"]
Mutable = Literal["inherit"] | bool
Object = Mapping[str, object]
T = TypeVar("T")


class _Value:
    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("Use from_json to construct validated managed values")


def _new(cls: type[T], **values: object) -> T:
    result = object.__new__(cls)
    for key, value in values.items():
        object.__setattr__(result, key, value)
    return result


def _error(message: str, location: str = "", code: str = "schema_invalid") -> GraphError:
    return GraphError(code, message, location=location)


def _obj(value: object, required: str, optional: str = "", loc: str = "") -> Object:
    if not isinstance(value, Mapping) or any(type(k) is not str for k in value):
        raise _error("Expected an object", loc)
    keys = set(value)
    if not set(required.split()) <= keys or keys - set((required + " " + optional).split()):
        raise _error("Missing required or unknown core fields", loc)
    return cast(Object, value)


def _array(value: object, loc: str = "") -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise _error("Expected an array", loc)
    return tuple(value)


def _text(value: object, loc: str = "", *, empty: bool = False) -> str:
    if type(value) is not str or (not value and not empty):
        raise _error("Expected a nonempty string" if not empty else "Expected a string", loc)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _error("Expected Unicode scalar text", loc) from None
    return value


def _name(value: object, loc: str = "") -> str:
    text = _text(value, loc)
    if any(c == "/" or ord(c) < 32 or 127 <= ord(c) <= 159 for c in text):
        raise _error("Names cannot contain slash or control characters", loc)
    return text


def _choice(value: object, choices: str, loc: str = "") -> str:
    if type(value) is not str or value not in choices.split():
        raise _error("Invalid enum value", loc)
    return value


def _bool(value: object, loc: str = "") -> bool:
    if type(value) is not bool:
        raise _error("Expected a boolean", loc)
    return value


def _ids(value: object, loc: str = "") -> tuple[str, ...]:
    result = tuple(validate_id(v, location=loc) for v in _array(value, loc))
    if len(set(result)) != len(result):
        raise _error("Duplicate identity", loc)
    return result


def _json_object(value: object, loc: str = "") -> Mapping[str, FrozenJSON]:
    if not isinstance(value, Mapping):
        raise _error("Expected a JSON object", loc)
    return cast(Mapping[str, FrozenJSON], freeze_json(value))


def _extensions(value: object, loc: str = "") -> Mapping[str, FrozenJSON]:
    result = _json_object(value, loc)
    if any(re.fullmatch(r"[a-z][a-z0-9.-]*:[A-Za-z0-9_.-]+", k) is None for k in result):
        raise _error("Extension keys must be namespace:key", loc)
    # Extensions here are inert JSON metadata. A compiler must negotiate any
    # execution-affecting semantics; accepting metadata does not execute it.
    return result


@dataclass(frozen=True, slots=True, init=False)
class TextPart(_Value):
    text: str

    def as_json(self) -> dict[str, object]:
        return {"kind": "Text", "text": self.text}


@dataclass(frozen=True, slots=True, init=False)
class NodeRefPart(_Value):
    target_id: str
    label: str | None

    def as_json(self) -> dict[str, object]:
        value: dict[str, object] = {"kind": "NodeRef", "target_id": self.target_id}
        if self.label is not None:
            value["label"] = self.label
        return value


TemplatePart = TextPart | NodeRefPart


@dataclass(frozen=True, slots=True, init=False)
class InstructionTemplate(_Value):
    parts: tuple[TemplatePart, ...]

    @classmethod
    def from_json(cls, value: object) -> InstructionTemplate:
        obj = _obj(value, "parts")
        parts: list[TemplatePart] = []
        for raw in _array(obj["parts"]):
            if not isinstance(raw, Mapping):
                raise _error("Expected a template part")
            if raw.get("kind") == "Text":
                part = _obj(raw, "kind text")
                parts.append(_new(TextPart, text=_text(part["text"], empty=True)))
            elif raw.get("kind") == "NodeRef":
                part = _obj(raw, "kind target_id", "label")
                parts.append(_new(NodeRefPart, target_id=validate_id(part["target_id"]),
                                  label=_text(part["label"], empty=True) if "label" in part else None))
            else:
                raise _error("Unknown template part kind")
        return _new(cls, parts=tuple(parts))

    def as_json(self) -> dict[str, object]:
        return {"parts": [p.as_json() for p in self.parts]}


@dataclass(frozen=True, slots=True, init=False)
class Members(_Value):
    pre_process: str | None
    children: tuple[str, ...]
    post_process: str | None
    skills: tuple[str, ...]
    tools: tuple[str, ...]
    _placements: tuple[tuple[Slot, int, str], ...] | None = field(
        default=None, init=False, repr=False, compare=False)

    @classmethod
    def from_json(cls, value: object) -> Members:
        obj = _obj(value, "", "pre_process children post_process skills tools")
        return _new(cls, pre_process=validate_id(obj["pre_process"]) if "pre_process" in obj else None,
                    children=_ids(obj.get("children", ())),
                    post_process=validate_id(obj["post_process"]) if "post_process" in obj else None,
                    skills=_ids(obj.get("skills", ())), tools=_ids(obj.get("tools", ())),
                    _placements=None)

    def placements(self) -> tuple[tuple[Slot, int, str], ...]:
        cached = self._placements
        if cached is None:
            cached = self._compute_placements()
            object.__setattr__(self, "_placements", cached)
        return cached

    def _compute_placements(self) -> tuple[tuple[Slot, int, str], ...]:
        result: list[tuple[Slot, int, str]] = []
        if self.pre_process is not None:
            result.append(("pre_process", 0, self.pre_process))
        result.extend(("children", i, n) for i, n in enumerate(self.children))
        if self.post_process is not None:
            result.append(("post_process", 0, self.post_process))
        result.extend(("skills", i, n) for i, n in enumerate(self.skills))
        result.extend(("tools", i, n) for i, n in enumerate(self.tools))
        return tuple(result)

    def as_json(self) -> dict[str, object]:
        value: dict[str, object] = {"children": list(self.children), "skills": list(self.skills),
                                    "tools": list(self.tools)}
        if self.pre_process is not None:
            value["pre_process"] = self.pre_process
        if self.post_process is not None:
            value["post_process"] = self.post_process
        return value


@dataclass(frozen=True, slots=True, init=False)
class Portability(_Value):
    kind: Literal["portable", "native"]
    contract_id: str | None
    language: str | None
    version: str | None
    factory: str | None

    @classmethod
    def from_json(cls, value: object) -> Portability:
        if not isinstance(value, Mapping):
            raise _error("Expected portability descriptor")
        kind = _choice(value.get("kind"), "portable native")
        if kind == "portable":
            obj = _obj(value, "kind contract_id")
            return _new(cls, kind=kind, contract_id=validate_id(obj["contract_id"]),
                        language=None, version=None, factory=None)
        obj = _obj(value, "kind language version factory")
        return _new(cls, kind=kind, contract_id=None,
                    language=_choice(obj["language"], "python typescript go"),
                    version=_text(obj["version"]), factory=_text(obj["factory"]))

    def as_json(self) -> dict[str, object]:
        if self.kind == "portable":
            return {"kind": self.kind, "contract_id": self.contract_id}
        return {"kind": self.kind, "language": self.language,
                "version": self.version, "factory": self.factory}


@dataclass(frozen=True, slots=True, init=False)
class BindingDescriptor(_Value):
    kind: Literal["registered", "remote"]
    binding_id: str
    version: str
    portability: Portability
    config: Mapping[str, FrozenJSON]
    secret_refs: tuple[str, ...]
    expected_contract_digest: str | None

    @classmethod
    def from_json(cls, value: object) -> BindingDescriptor:
        obj = _obj(value, "kind binding_id version portability",
                   "config secret_refs expected_contract_digest")
        digest = None
        if "expected_contract_digest" in obj:
            digest = _text(obj["expected_contract_digest"])
            if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
                raise _error("Invalid expected contract digest")
        return _new(cls, kind=_choice(obj["kind"], "registered remote"),
                    binding_id=validate_id(obj["binding_id"]), version=_text(obj["version"]),
                    portability=Portability.from_json(obj["portability"]),
                    config=_json_object(obj.get("config", {})),
                    secret_refs=_ids(obj.get("secret_refs", ())), expected_contract_digest=digest)

    def as_json(self) -> dict[str, object]:
        value: dict[str, object] = {"kind": self.kind, "binding_id": self.binding_id,
            "version": self.version, "portability": self.portability.as_json(),
            "config": thaw_json(self.config), "secret_refs": list(self.secret_refs)}
        if self.expected_contract_digest is not None:
            value["expected_contract_digest"] = self.expected_contract_digest
        return value


@dataclass(frozen=True, slots=True, init=False)
class NodeSpec(_Value):
    node_id: str
    kind: Kind
    name: str
    description: str
    mutable: Mutable
    uses: tuple[str, ...]
    extensions: Mapping[str, FrozenJSON]
    instructions: str | None
    instruction_template: InstructionTemplate | None
    role_variant: str | None
    members: Members | None
    binding: BindingDescriptor | None
    input_contract: bool | Mapping[str, FrozenJSON] | None
    _bytes: bytes | None = field(default=None, init=False, repr=False, compare=False)

    def canonical_bytes(self) -> bytes:
        """Memoized JCS bytes of as_json(): the node's exact semantic identity.

        Equal bytes mean an equal node (JCS keeps True and 1 distinct, unlike
        Python ==). Unchanged nodes are shared between revisions, so this is
        computed once per distinct node, not once per revision.
        """
        cached = self._bytes
        if cached is None:
            cached = canonical_json(self.as_json())
            object.__setattr__(self, "_bytes", cached)
        return cached

    def same(self, other: NodeSpec) -> bool:
        return self is other or self.canonical_bytes() == other.canonical_bytes()

    @classmethod
    def from_json(cls, value: object) -> NodeSpec:
        obj = _obj(value, "node_id kind name description",
                   "mutable uses extensions instructions instruction_template role_variant "
                   "members binding input_contract")
        kind = _choice(obj["kind"], "role skill tool")
        mutable = obj.get("mutable", "inherit")
        if type(mutable) is not bool and mutable != "inherit":
            raise _error("mutable must be boolean or inherit")
        if kind in ("role", "skill"):
            if ("instructions" in obj) == ("instruction_template" in obj):
                raise _error("Supply exactly one instruction representation")
            if "binding" in obj or "input_contract" in obj:
                raise _error("Only Tools have execution assertions")
        if kind != "role" and ("members" in obj or "role_variant" in obj):
            raise _error("Only Roles contain members or have a variant")
        if kind == "tool" and ("instructions" in obj or "instruction_template" in obj):
            raise _error("Tools cannot have instructions")
        contract = None
        if "input_contract" in obj:
            raw = obj["input_contract"]
            contract = raw if type(raw) is bool else _json_object(raw)
            try:
                # Check assertion syntax only. Never fetch $refs or compile an
                # author schema as an independent execution validator.
                assertion = thaw_json(contract)
                if not isinstance(assertion, (bool, dict)):
                    raise _error("Input contract must be a boolean or object")
                Draft202012Validator.check_schema(assertion)
            except SchemaError:
                raise _error("Invalid JSON Schema input contract assertion") from None
            except RecursionError:
                raise _error("Input contract exceeds processing budget", code="limit_exceeded") from None
        return _new(cls, node_id=validate_id(obj["node_id"]), kind=kind,
            name=_name(obj["name"]), description=_text(obj["description"]), mutable=mutable,
            uses=_ids(obj.get("uses", ())), extensions=_extensions(obj.get("extensions", {})),
            instructions=_text(obj["instructions"], empty=True) if "instructions" in obj else None,
            instruction_template=InstructionTemplate.from_json(obj["instruction_template"])
                if "instruction_template" in obj else None,
            role_variant=_choice(obj.get("role_variant", "ordinary"),
                "ordinary pre_process post_process") if kind == "role" else None,
            members=Members.from_json(obj.get("members", {})) if kind == "role" else None,
            binding=BindingDescriptor.from_json(obj["binding"]) if "binding" in obj else None,
            input_contract=contract, _bytes=None)

    def as_json(self) -> dict[str, object]:
        value: dict[str, object] = {"node_id": self.node_id, "kind": self.kind,
            "name": self.name, "description": self.description, "mutable": self.mutable,
            "uses": list(self.uses), "extensions": thaw_json(self.extensions)}
        if self.instructions is not None:
            value["instructions"] = self.instructions
        if self.instruction_template is not None:
            value["instruction_template"] = self.instruction_template.as_json()
        if self.members is not None:
            value["members"] = self.members.as_json()
            value["role_variant"] = self.role_variant
        if self.binding is not None:
            value["binding"] = self.binding.as_json()
        if self.input_contract is not None:
            value["input_contract"] = thaw_json(self.input_contract)
        return value


@dataclass(frozen=True, slots=True, init=False)
class RootEntry(_Value):
    node_id: str
    plane: Plane

    @classmethod
    def from_json(cls, value: object) -> RootEntry:
        obj = _obj(value, "node_id plane")
        return _new(cls, node_id=validate_id(obj["node_id"]),
                    plane=_choice(obj["plane"], "model prompt"))

    def as_json(self) -> dict[str, object]:
        return {"node_id": self.node_id, "plane": self.plane}


@dataclass(frozen=True, slots=True, init=False)
class PromptPublication(_Value):
    publication_id: str
    name: str
    description: str
    opens_id: str
    model_may_open: bool

    def as_json(self) -> dict[str, object]:
        return {"kind": "prompt", "publication_id": self.publication_id, "name": self.name,
                "description": self.description, "opens_id": self.opens_id,
                "model_may_open": self.model_may_open}


@dataclass(frozen=True, slots=True, init=False)
class ResourcePublication(_Value):
    publication_id: str
    name: str
    uri: str
    mime_type: str
    opens_id: str

    def as_json(self) -> dict[str, object]:
        return {"kind": "resource", "publication_id": self.publication_id, "name": self.name,
                "uri": self.uri, "mime_type": self.mime_type, "opens_id": self.opens_id}


@dataclass(frozen=True, slots=True, init=False)
class RoutePublication(_Value):
    publication_id: str
    method: str
    path: str
    opens_id: str

    def as_json(self) -> dict[str, object]:
        return {"kind": "route", "publication_id": self.publication_id,
                "method": self.method, "path": self.path, "opens_id": self.opens_id}


Publication = PromptPublication | ResourcePublication | RoutePublication


def _resource_uri(value: object) -> str:
    """Check absolute URI syntax without a transport parser or resolution.

    Preserve the former split-only checks, including bracketed IP literals
    and NFKC authority delimiters. Do not resolve hosts, decode escapes,
    normalize author text, or impose scheme-specific host/port requirements.
    """
    uri = _text(value)
    scheme = re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", uri)
    if scheme is None or any(c.isspace() or ord(c) < 32 for c in uri):
        raise _error("Resource URI must be absolute without whitespace")
    rest = uri[scheme.end():]
    if not rest.startswith("//"):
        return uri
    authority = re.split(r"[/?#]", rest[2:], maxsplit=1)[0]
    try:
        if ("[" in authority) != ("]" in authority):
            raise ValueError("Unbalanced IP literal")
        if "[" in authority:
            host_port = authority.rpartition("@")[2]
            before, opening, bracketed = host_port.partition("[")
            if opening:
                host, _, port = bracketed.partition("]")
                if before or (port and not port.startswith(":")):
                    raise ValueError("Invalid bracketed authority")
            else:
                host = host_port.partition(":")[0]
            if host.startswith(("v", "V")):
                if re.fullmatch(r"[vV][a-fA-F0-9]+\..+", host) is None:
                    raise ValueError("Invalid IPvFuture literal")
            else:
                IPv6Address(host)
        # Existing delimiters are legal; compatibility characters that expand
        # into delimiters are not. This checks syntax without rewriting it.
        plain = authority.translate(str.maketrans("", "", "@:#?"))
        if any(c in normalize("NFKC", plain) for c in "/?#@:"):
            raise ValueError("Invalid authority under NFKC normalization")
    except ValueError:
        raise _error("Invalid resource URI") from None
    return uri


def _publication(value: object) -> Publication:
    if not isinstance(value, Mapping):
        raise _error("Expected publication")
    kind = _choice(value.get("kind"), "prompt resource route")
    if kind == "prompt":
        obj = _obj(value, "kind publication_id name description opens_id model_may_open")
        return _new(PromptPublication, publication_id=validate_id(obj["publication_id"]),
            name=_name(obj["name"]), description=_text(obj["description"]),
            opens_id=validate_id(obj["opens_id"]), model_may_open=_bool(obj["model_may_open"]))
    if kind == "resource":
        obj = _obj(value, "kind publication_id name uri mime_type opens_id")
        uri = _resource_uri(obj["uri"])
        return _new(ResourcePublication, publication_id=validate_id(obj["publication_id"]),
            name=_name(obj["name"]), uri=uri, mime_type=_text(obj["mime_type"]),
            opens_id=validate_id(obj["opens_id"]))
    obj = _obj(value, "kind publication_id method path opens_id")
    path = _text(obj["path"])
    if not path.startswith("/"):
        raise _error("Route path must start with slash")
    return _new(RoutePublication, publication_id=validate_id(obj["publication_id"]),
        method=_choice(obj["method"], "GET HEAD POST PUT PATCH DELETE"), path=path,
        opens_id=validate_id(obj["opens_id"]))


# RFC 5646 grandfathered forms are valid even when they do not fit langtag.
_GRANDFATHERED = {s.lower(): s for s in (
    "en-GB-oed i-ami i-bnn i-default i-enochian i-hak i-klingon i-lux i-mingo "
    "i-navajo i-pwn i-tao i-tay i-tsu sgn-BE-FR sgn-BE-NL sgn-CH-DE "
    "art-lojban cel-gaulish no-bok no-nyn zh-guoyu zh-hakka zh-min zh-min-nan zh-xiang"
).split()}


def normalize_locale(value: object) -> str:
    """RFC 5646 well-formedness and conventional casing, not IANA alias rewriting.

    Registry validity/preferred-value replacement requires a frozen registry
    contract; the candidate spec has not assigned one. No locale is dropped.
    """
    text = _text(value)
    lower = text.lower()
    if lower in _GRANDFATHERED:
        return _GRANDFATHERED[lower]
    parts = text.split("-")
    if any(not p.isascii() or not p.isalnum() or not 1 <= len(p) <= 8 for p in parts):
        raise _error("Invalid BCP 47 locale")
    if parts[0].lower() == "x":
        if len(parts) == 1:
            raise _error("Private-use locale requires a subtag")
        return lower
    lang = parts[0]
    if not lang.isalpha() or not 2 <= len(lang) <= 8:
        raise _error("Invalid locale language")
    result = [lang.lower()]
    i = 1
    if len(lang) <= 3:
        for _ in range(3):
            if i < len(parts) and len(parts[i]) == 3 and parts[i].isalpha():
                result.append(parts[i].lower())
                i += 1
            else:
                break
    if i < len(parts) and len(parts[i]) == 4 and parts[i].isalpha():
        result.append(parts[i].title())
        i += 1
    if i < len(parts) and ((len(parts[i]) == 2 and parts[i].isalpha())
                          or (len(parts[i]) == 3 and parts[i].isdigit())):
        result.append(parts[i].upper())
        i += 1
    variants: set[str] = set()
    while i < len(parts) and (len(parts[i]) >= 5 or
                             (len(parts[i]) == 4 and parts[i][0].isdigit())):
        item = parts[i].lower()
        if item in variants:
            raise _error("Duplicate locale variant")
        variants.add(item)
        result.append(item)
        i += 1
    singletons: set[str] = set()
    while i < len(parts):
        singleton = parts[i].lower()
        if len(singleton) != 1 or singleton in singletons:
            raise _error("Invalid or duplicate locale extension")
        singletons.add(singleton)
        result.append(singleton)
        i += 1
        start = i
        while i < len(parts) and (singleton == "x" or len(parts[i]) >= 2):
            result.append(parts[i].lower())
            i += 1
        if i == start:
            raise _error("Locale extension requires subtags")
    return "-".join(result)


@dataclass(frozen=True, slots=True, init=False)
class Presentation(_Value):
    node_id: str
    title: str | None
    description: str | None

    def as_json(self) -> dict[str, object]:
        value: dict[str, object] = {"node_id": self.node_id}
        if self.title is not None:
            value["title"] = self.title
        if self.description is not None:
            value["description"] = self.description
        return value


@dataclass(frozen=True, slots=True, init=False)
class LocaleCatalog(_Value):
    locale: str
    entries: tuple[Presentation, ...]

    def as_json(self) -> dict[str, object]:
        return {"locale": self.locale, "entries": [e.as_json() for e in self.entries]}


@dataclass(frozen=True, slots=True, init=False)
class Presentations(_Value):
    default_locale: str
    catalogs: tuple[LocaleCatalog, ...]

    @classmethod
    def from_json(cls, value: object) -> Presentations:
        obj = _obj(value, "default_locale catalogs")
        catalogs: list[LocaleCatalog] = []
        locales: set[str] = set()
        for raw in _array(obj["catalogs"]):
            catalog = _obj(raw, "locale entries")
            locale = normalize_locale(catalog["locale"])
            if locale.lower() in locales:
                raise _error("Duplicate normalized locale")
            locales.add(locale.lower())
            entries: list[Presentation] = []
            ids: set[str] = set()
            for entry in _array(catalog["entries"]):
                e = _obj(entry, "node_id", "title description")
                if "title" not in e and "description" not in e:
                    raise _error("Presentation requires title or description")
                node_id = validate_id(e["node_id"])
                if node_id in ids:
                    raise _error("Duplicate presentation target")
                ids.add(node_id)
                entries.append(_new(Presentation, node_id=node_id,
                    title=_text(e["title"]) if "title" in e else None,
                    description=_text(e["description"]) if "description" in e else None))
            catalogs.append(_new(LocaleCatalog, locale=locale, entries=tuple(entries)))
        return _new(cls, default_locale=normalize_locale(obj["default_locale"]),
                    catalogs=tuple(catalogs))

    def as_json(self) -> dict[str, object]:
        return {"default_locale": self.default_locale,
                "catalogs": [c.as_json() for c in self.catalogs]}


@dataclass(frozen=True, slots=True)
class PublicationContract:
    """Trusted pinned-registry evidence for one exact binding descriptor.

    Not serialized into the definition and not invocation authority. The compiler
    must obtain this from its registry, not convert input_contract into evidence.
    """

    binding: BindingDescriptor
    effect: Literal["read_only", "write"]
    argument_free: bool

    def __post_init__(self) -> None:
        if (not isinstance(self.binding, BindingDescriptor)
                or self.effect not in ("read_only", "write")
                or type(self.argument_free) is not bool):
            raise _error("Invalid publication contract evidence")


ContractResolver = Callable[["BindingDescriptor"], "PublicationContract | None"]

class ContractEvidence:
    """Normalized registry evidence: get(binding) -> exact contract or None."""

    __slots__ = ("_static", "_resolver")

    def __init__(self, contracts: PublicationContracts | None = None) -> None:
        self._resolver: ContractResolver | None = None
        self._static: dict[bytes, PublicationContract] = {}
        if contracts is None:
            return
        if isinstance(contracts, ContractEvidence):
            self._resolver, self._static = contracts._resolver, dict(contracts._static)
            return
        if callable(contracts) and not isinstance(contracts, Mapping):
            self._resolver = contracts
            return
        values: Iterable[object] = contracts.values() if isinstance(contracts, Mapping) else contracts
        for contract in values:
            if not isinstance(contract, PublicationContract):
                raise _error("Invalid publication contract evidence", code="contract_mismatch")
            key = canonical_json(contract.binding.as_json())
            if self._static.setdefault(key, contract) != contract:
                raise _error("Conflicting registry evidence for one binding", code="contract_mismatch")

    def __len__(self) -> int:
        return len(self._static)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, ContractEvidence) and self._static == other._static
                and self._resolver is other._resolver)

    __hash__ = None  # type: ignore[assignment]

    def get(self, binding: BindingDescriptor) -> PublicationContract | None:
        contract = self._static.get(canonical_json(binding.as_json()))
        if contract is None and self._resolver is not None:
            contract = self._resolver(binding)
        if contract is not None and (not isinstance(contract, PublicationContract)
                                     or contract.binding != binding):
            raise _error("Registry evidence does not match the exact binding", code="contract_mismatch")
        return contract


PublicationContracts = (Mapping[str, PublicationContract] | Iterable[PublicationContract]
                        | ContractResolver | ContractEvidence)
"""Registry evidence: contracts, a mapping whose values are contracts, or a
resolver (e.g. backed by a pinned RegistryGeneration) for exact descriptors.

Evidence is indexed by the EXACT binding descriptor (including config), never
by node identity, so a registry may vouch for several versions of a binding at
once (e.g. while a Tool is upgraded in one transaction). Mapping keys are not
semantic.
"""


def contract_lookup(contracts: PublicationContracts | None) -> ContractEvidence:
    """Normalize registry evidence once (idempotent)."""
    return contracts if isinstance(contracts, ContractEvidence) else ContractEvidence(contracts)


@dataclass(frozen=True, slots=True, init=False)
class GraphDefinition(_Value):
    schema: str
    semantic_version: str
    graph_id: str
    application_name: str
    mode: Literal["runtime", "disclosure_only"]
    nodes: Mapping[str, NodeSpec]
    roots: tuple[RootEntry, ...]
    publications: tuple[Publication, ...]
    presentations: Presentations
    extensions: Mapping[str, FrozenJSON]
    _cached_digest: str | None = field(default=None, init=False, repr=False, compare=False)
    _cached_size: int | None = field(default=None, init=False, repr=False, compare=False)

    @classmethod
    def from_json(
        cls, value: object, *, publication_contracts: PublicationContracts | None = None,
        verify_contracts: bool = True,
    ) -> GraphDefinition:
        """Strictly decode bytes or detach native JSON and validate a committed forest.

        Native JSON has already lost numeric lexemes; raw imports should use
        bytes. Empty drafts are deliberately not accepted as serving definitions.
        `verify_contracts=False` is ONLY for already accepted history (replay,
        transaction bases): registry evidence is admission/compile input, so a
        later registry must never make an accepted journal unreadable.
        """
        if isinstance(value, bytes):
            value = decode_json(value)
        # Detach and reject invalid Unicode/numbers/cyclic Python JSON before
        # inspecting shape. Flat node pools keep graph depth out of JSON depth.
        value = freeze_json(value)
        obj = _obj(value, "schema semantic_version graph_id application_name mode nodes roots presentations",
                   "publications extensions")
        nodes: dict[str, NodeSpec] = {}
        for i, raw in enumerate(_array(obj["nodes"], "/nodes")):
            try:
                node = NodeSpec.from_json(raw)
            except GraphError as exc:
                raise _error(str(exc), f"/nodes/{i}", exc.code) from None
            if node.node_id in nodes:
                raise _error("Duplicate node identity", f"/nodes/{i}/node_id")
            nodes[node.node_id] = node
        return cls._from_parts(obj, nodes, publication_contracts=publication_contracts,
                               verify_contracts=verify_contracts)

    @classmethod
    def assemble(
        cls, header: Mapping[str, object], base: Mapping[str, NodeSpec],
        changed: Mapping[str, Mapping[str, object] | None], *,
        reuse: Mapping[str, NodeSpec] | None = None,
        publication_contracts: PublicationContracts | None = None, verify_contracts: bool = True,
    ) -> GraphDefinition:
        """Build a definition from shared validated nodes plus changed node wires.

        `header` is the graph wire WITHOUT `nodes`; `base` are already validated
        NodeSpecs kept as-is. `changed` maps an identity to its new node wire, or
        None to remove it. Every changed wire is fully validated; one equal to
        its `reuse` (default: `base`) node reuses that object, so unchanged nodes
        are shared across revisions. Forest validation is identical to from_json.
        """
        reuse = base if reuse is None else reuse
        value = freeze_json(header)
        obj = _obj(value, "schema semantic_version graph_id application_name mode roots presentations",
                   "publications extensions")
        nodes = dict(base)
        for identity in sorted(changed):
            raw = changed[identity]
            if raw is None:
                nodes.pop(identity, None)
                continue
            try:
                node = NodeSpec.from_json(raw)
            except GraphError as exc:
                raise _error(str(exc), "/nodes", exc.code) from None
            if node.node_id != identity:
                raise _error("Duplicate node identity", "/nodes")
            previous = reuse.get(identity)
            nodes[identity] = previous if previous is not None and previous.same(node) else node
        return cls._from_parts(obj, nodes, publication_contracts=publication_contracts,
                               verify_contracts=verify_contracts)

    @classmethod
    def _from_parts(cls, obj: Object, nodes: dict[str, NodeSpec], *,
                    publication_contracts: PublicationContracts | None,
                    verify_contracts: bool) -> GraphDefinition:
        if obj["schema"] != "contexture.graph/v1" or obj["semantic_version"] != "1":
            raise _error("Unsupported graph schema or semantic version")
        definition = _new(cls, schema="contexture.graph/v1", semantic_version="1",
            graph_id=validate_id(obj["graph_id"], location="/graph_id"),
            application_name=_name(obj["application_name"], "/application_name"),
            mode=_choice(obj["mode"], "runtime disclosure_only", "/mode"),
            nodes=MappingProxyType(nodes),
            roots=tuple(RootEntry.from_json(r) for r in _array(obj["roots"], "/roots")),
            publications=tuple(_publication(p) for p in _array(obj.get("publications", ()))),
            presentations=Presentations.from_json(obj["presentations"]),
            extensions=_extensions(obj.get("extensions", {}), "/extensions"), _cached_digest=None,
            _cached_size=None)
        # Local import avoids a module initialization cycle. Build is pure and
        # uses the same whole-forest validator as callers' explicit indexes.
        from .index import GraphIndex

        GraphIndex.build(definition, publication_contracts=publication_contracts,
                         verify_contracts=verify_contracts)
        return definition

    def as_json(self) -> dict[str, object]:
        """Detached normalized wire value; node order is Unicode code-point order."""
        return {"schema": self.schema, "semantic_version": self.semantic_version,
            "graph_id": self.graph_id, "application_name": self.application_name, "mode": self.mode,
            "nodes": [self.nodes[n].as_json() for n in sorted(self.nodes)],
            "roots": [r.as_json() for r in self.roots],
            "publications": [p.as_json() for p in self.publications],
            "presentations": self.presentations.as_json(), "extensions": thaw_json(self.extensions)}

    def header(self) -> dict[str, object]:
        """Detached graph wire without `nodes` (for assemble and graph-level facts)."""
        return {"schema": self.schema, "semantic_version": self.semantic_version,
            "graph_id": self.graph_id, "application_name": self.application_name, "mode": self.mode,
            "roots": [r.as_json() for r in self.roots],
            "publications": [p.as_json() for p in self.publications],
            "presentations": self.presentations.as_json(), "extensions": thaw_json(self.extensions)}

    def _pieces(self) -> list[bytes]:
        """JCS bytes of as_json() as pieces around the shared node bytes.

        JCS sorts object keys, so the top-level object is exactly the keys
        before "nodes", then "nodes", then the keys after it.
        """
        header = self.header()
        before = canonical_json({k: v for k, v in header.items() if k < "nodes"})
        after = canonical_json({k: v for k, v in header.items() if k > "nodes"})
        pieces = [before[:-1], b',"nodes":[']
        for i, identity in enumerate(sorted(self.nodes)):
            if i:
                pieces.append(b",")
            pieces.append(self.nodes[identity].canonical_bytes())
        pieces.extend((b"],", after[1:]))
        return pieces

    def canonical_bytes(self) -> bytes:
        """Exact JCS bytes of as_json() (not retained: revisions share nodes)."""
        return b"".join(self._pieces())

    @property
    def canonical_size(self) -> int:
        """len(canonical_bytes()), memoized with the digest."""
        self.digest()
        return cast(int, self._cached_size)

    def digest(self) -> str:
        digest = self._cached_digest
        if digest is None:
            # Stream the hash: retaining joined bytes per revision would cost
            # revisions x graph size, defeating node sharing in History.
            hasher = hashlib.sha256(b"contexture.graph/v1\0")
            size = 0
            for piece in self._pieces():
                hasher.update(piece)
                size += len(piece)
            digest = "sha256:" + hasher.hexdigest()
            object.__setattr__(self, "_cached_size", size)
            object.__setattr__(self, "_cached_digest", digest)
        return digest

    @property
    def definition_digest(self) -> str:
        return self.digest()