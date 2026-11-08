"""Pinned WP07 contracts, not resource preparation or a graph compiler.

Public wire values follow managed.schema.json's BindingEntry/ExecutionManifest.
The binding/registry hash domains below are PROVISIONAL IMPLEMENTATION contracts,
not accepted normative managed domains. They hash domain + NUL + managed JCS.
Their explicit preimages include decoder/default/coercion/config/sharing policy
because the public BindingEntry intentionally exposes only a contract digest.
No alternate public wire format is introduced for these private preimages.

Trusted callables are captured by reference, never serialized or called here.
Their implementation/semantic IDs must change when behavior changes. Capturing
does not freeze service state or closure cells: deployments must supply stable,
reentrant callables. Resources and secret material belong to separate preparation;
secret_refs here are authorized opaque HANDLE identities, never resolved values.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import re
from types import MappingProxyType
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource, Unresolvable
from referencing.jsonschema import DRAFT202012

from .canonical import (
    FrozenJSON, canonical_digest, canonical_json, decode_json, freeze_json, thaw_json, validate_id,
)
from .errors import GraphError
from .model import BindingDescriptor, Portability, PublicationContract

BINDING_CONTRACT_DOMAIN = "contexture.binding-contract/implementation-v1"
REGISTRY_CONTRACT_DOMAIN = "contexture.registry-contract/implementation-v1"
Schema = bool | Mapping[str, FrozenJSON]
ValidatorSchema = bool | Mapping[str, Any]
Effect = Literal["read_only", "write"]
TrustedCallable = Callable[..., object]


def _fail(message: str, code: str = "binding_contract_invalid") -> GraphError:
    return GraphError(code, message)


def _text(value: object) -> str:
    if type(value) is not str or not value:
        raise _fail("Expected nonempty scalar text")
    freeze_json(value)
    return value


def _digest(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise _fail("Expected a SHA256 digest")
    return value


def _object(value: object, fields: str) -> Mapping[str, object]:
    if isinstance(value, bytes):
        value = decode_json(value)
    value = freeze_json(value)
    if not isinstance(value, Mapping) or set(value) != set(fields.split()):
        raise _fail("Missing required or unknown contract fields")
    return value


def _ids(values: object) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        raise _fail("Expected an identity array")
    result = tuple(validate_id(v) for v in values)
    if len(set(result)) != len(result):
        raise _fail("Duplicate identity")
    return result


def _private_digest(value: object, domain: str) -> str:
    """Provisional private preimages only; never extend canonical.DOMAINS here."""
    return "sha256:" + hashlib.sha256(domain.encode() + b"\0" + canonical_json(value)).hexdigest()


def _no_retrieve(uri: str) -> Resource[ValidatorSchema]:
    raise NoSuchResource(ref=uri)


def _validator(schema: Schema) -> Draft202012Validator:
    raw = cast(ValidatorSchema, thaw_json(schema))
    return Draft202012Validator(raw, registry=Registry(retrieve=_no_retrieve))


def _schema(value: object) -> Schema:
    """Detach/check Draft 2020-12 with local JSON-pointer refs only, no retrieval.

    This bounded implementation rejects anchors, dynamic refs and $id rebasing;
    their full portable negotiation is not established by this slice. $ref in
    instance data (e.g. const/default) is not mistaken for a schema reference.
    """
    frozen = freeze_json(value)
    if type(frozen) is not bool and not isinstance(frozen, Mapping):
        raise _fail("Expected a boolean or object JSON Schema")
    schema = cast(Schema, frozen)
    raw = cast(ValidatorSchema, thaw_json(schema))
    try:
        Draft202012Validator.check_schema(raw)
        resource = Resource.from_contents(raw, default_specification=DRAFT202012)
        resolver = Registry(retrieve=_no_retrieve).resolver_with_root(resource)
        pending = [resource]
        visited: set[int] = set()
        while pending:
            current = pending.pop()
            contents = current.contents
            if id(contents) in visited:
                continue
            visited.add(id(contents))
            if isinstance(contents, dict):
                if any(k in contents for k in ("$id", "$anchor", "$dynamicAnchor", "$dynamicRef")):
                    raise _fail("Schema identifiers/anchors/dynamic refs are unsupported")
                if "$schema" in contents and contents["$schema"] != "https://json-schema.org/draft/2020-12/schema":
                    raise _fail("Unsupported JSON Schema dialect")
                if "$ref" in contents:
                    ref = contents["$ref"]
                    if not isinstance(ref, str) or (ref != "#" and not ref.startswith("#/")):
                        raise _fail("Only local JSON-pointer schema refs are supported")
                    target = resolver.lookup(ref).contents
                    if type(target) is not bool and not isinstance(target, dict):
                        raise _fail("Schema ref does not identify a schema")
                    Draft202012Validator.check_schema(target)
                    # A pointer may enter an otherwise data-valued keyword;
                    # referenced contents become schemas and need the same gate.
                    pending.append(Resource.from_contents(target, default_specification=DRAFT202012))
            pending.extend(current.subresources())
    except (SchemaError, Unresolvable, NoSuchResource):
        raise _fail("Invalid or unresolved JSON Schema") from None
    except RecursionError:
        raise _fail("Schema exceeds processing budget", "limit_exceeded") from None
    return schema


def _validate(schema: Schema, value: object) -> None:
    try:
        _validator(schema).validate(thaw_json(value))
    except (ValidationError, Unresolvable, NoSuchResource):
        # Do not echo instance data, configuration values or secret handles.
        raise _fail("Value does not satisfy the registered schema") from None
    except RecursionError:
        raise _fail("Schema validation exceeds processing budget", "limit_exceeded") from None


@dataclass(frozen=True, slots=True, kw_only=True)
class BindingRegistration:
    """Trusted code registration. No deserializer can manufacture callables.

    Contract IDs attest the complete validator/decoder/default/coercion semantics,
    including any argument-free claim; JSON Schema alone cannot prove that claim.
    `factory` is merely captured for a later resource-preparation slice. Remote
    registrations require an explicitly supplied adapter handler, never discovery.
    Config has two independent gates: top-level allowlist and an offline schema.
    """

    binding_id: str
    version: str
    kind: Literal["registered", "remote"]
    portability: Portability
    effect: Effect
    abi: str
    artifact_digest: str
    input_schema: Schema
    validator_contract_id: str
    decoder_contract_id: str
    default_contract_id: str
    coercion_contract_id: str
    handler: TrustedCallable = field(repr=False, compare=False)
    decoder: TrustedCallable = field(repr=False, compare=False)
    validator: TrustedCallable = field(repr=False, compare=False)
    factory: TrustedCallable | None = field(default=None, repr=False, compare=False)
    allowed_config: tuple[str, ...] = ()
    config_schema: Schema = field(default_factory=lambda: {
        "type": "object", "additionalProperties": False})
    allowed_secret_refs: tuple[str, ...] = ()
    resource_policy: Mapping[str, FrozenJSON] = field(default_factory=dict)
    argument_free: bool = False

    def __post_init__(self) -> None:
        validate_id(self.binding_id)
        for value in (self.version, self.abi):
            _text(value)
        for value in (self.validator_contract_id, self.decoder_contract_id,
                      self.default_contract_id, self.coercion_contract_id):
            validate_id(value)
        if self.kind not in ("registered", "remote") or self.effect not in ("read_only", "write"):
            raise _fail("Unsupported binding kind or effect")
        if not isinstance(self.portability, Portability) or type(self.argument_free) is not bool:
            raise _fail("Invalid portability or argument-free evidence")
        _digest(self.artifact_digest)
        if (not all(callable(v) for v in (self.handler, self.decoder, self.validator))
                or (self.factory is not None and not callable(self.factory))):
            raise _fail("Trusted handler, decoder, validator and optional factory must be callable")
        object.__setattr__(self, "input_schema", _schema(self.input_schema))
        object.__setattr__(self, "config_schema", _schema(self.config_schema))
        object.__setattr__(self, "allowed_config", tuple(sorted(_ids(self.allowed_config))))
        object.__setattr__(self, "allowed_secret_refs", tuple(sorted(_ids(self.allowed_secret_refs))))
        policy = freeze_json(self.resource_policy)
        if not isinstance(policy, Mapping):
            raise _fail("Resource sharing/cache policy must be a JSON object")
        object.__setattr__(self, "resource_policy", policy)
        if self.argument_free:
            _validate(self.input_schema, {})

    def _contract(self) -> dict[str, object]:
        """Private provisional identity preimage, not a public wire descriptor."""
        return {"binding_id": self.binding_id, "version": self.version, "kind": self.kind,
                "portability": self.portability.as_json(), "effect": self.effect,
                "abi": self.abi, "artifact_digest": self.artifact_digest,
                "input_schema": thaw_json(self.input_schema),
                "validator_contract_id": self.validator_contract_id,
                "decoder_contract_id": self.decoder_contract_id,
                "default_contract_id": self.default_contract_id,
                "coercion_contract_id": self.coercion_contract_id,
                "allowed_config": list(self.allowed_config),
                "config_schema": thaw_json(self.config_schema),
                "allowed_secret_refs": list(self.allowed_secret_refs),
                "resource_policy": thaw_json(self.resource_policy),
                "argument_free": self.argument_free, "has_factory": self.factory is not None}

    @property
    def contract_digest(self) -> str:
        return _private_digest(self._contract(), BINDING_CONTRACT_DOMAIN)


@dataclass(frozen=True, slots=True)
class BindingEntry:
    """Exact public BindingEntry fields; inert wire evidence, not execution authority."""

    binding: BindingDescriptor
    contract_digest: str
    artifact_digest: str
    effect: Effect
    abi: str
    input_schema: Schema
    validator_contract_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.binding, BindingDescriptor):
            raise _fail("Expected a validated binding descriptor")
        _digest(self.contract_digest)
        _digest(self.artifact_digest)
        if self.effect not in ("read_only", "write"):
            raise _fail("Unsupported effect")
        _text(self.abi)
        validate_id(self.validator_contract_id)
        object.__setattr__(self, "input_schema", _schema(self.input_schema))
        if (self.binding.expected_contract_digest is not None
                and self.binding.expected_contract_digest != self.contract_digest):
            raise _fail("Expected contract digest mismatch")

    @classmethod
    def from_json(cls, value: object) -> BindingEntry:
        obj = _object(value, "binding contract_digest artifact_digest effect abi input_schema validator_contract_id")
        return cls(BindingDescriptor.from_json(obj["binding"]), _digest(obj["contract_digest"]),
                   _digest(obj["artifact_digest"]), cast(Effect, obj["effect"]),
                   _text(obj["abi"]), cast(Schema, obj["input_schema"]),
                   validate_id(obj["validator_contract_id"]))

    def as_json(self) -> dict[str, object]:
        return {"binding": self.binding.as_json(), "contract_digest": self.contract_digest,
                "artifact_digest": self.artifact_digest, "effect": self.effect, "abi": self.abi,
                "input_schema": thaw_json(self.input_schema),
                "validator_contract_id": self.validator_contract_id}


@dataclass(frozen=True, slots=True)
class ResolvedBinding:
    """Pinned execution references; this type does not prepare or invoke anything."""

    entry: BindingEntry
    registration: BindingRegistration

    @property
    def publication_contract(self) -> PublicationContract:
        return PublicationContract(self.entry.binding, self.entry.effect,
                                   self.registration.argument_free)


@dataclass(frozen=True, slots=True, init=False)
class RegistryGeneration:
    """Immutable snapshot, exact (ID, version) lookup, including registry build.

    A fresh private mapping captures frozen registrations, not a live registry
    alias. Even unused registrations participate in identity. There is no latest
    alias, remote fetch, callable execution or resource factory construction.
    """

    build: str
    registrations: Mapping[tuple[str, str], BindingRegistration]
    registry_digest: str

    def __init__(self, *, build: str, registrations: Iterable[BindingRegistration]) -> None:
        build = _text(build)
        captured: dict[tuple[str, str], BindingRegistration] = {}
        for registration in registrations:
            if not isinstance(registration, BindingRegistration):
                raise _fail("Expected a trusted binding registration")
            key = (registration.binding_id, registration.version)
            if key in captured:
                raise _fail("Duplicate binding identity/version")
            captured[key] = registration
        digest = _private_digest({"build": build, "registrations": [
            captured[k]._contract() for k in sorted(captured)]}, REGISTRY_CONTRACT_DOMAIN)
        object.__setattr__(self, "build", build)
        object.__setattr__(self, "registrations", MappingProxyType(captured))
        object.__setattr__(self, "registry_digest", digest)

    def lookup(self, binding_id: str, version: str) -> BindingRegistration:
        key = (validate_id(binding_id), _text(version))
        try:
            return self.registrations[key]
        except KeyError:
            raise _fail("No registration for exact binding identity/version", "binding_not_found") from None

    def resolve(
        self, binding: BindingDescriptor, *, input_contract: object = None,
        expected_effect: Effect | None = None,
    ) -> ResolvedBinding:
        if not isinstance(binding, BindingDescriptor):
            raise _fail("Expected a validated binding descriptor")
        registration = self.lookup(binding.binding_id, binding.version)
        if binding.kind != registration.kind:
            raise _fail("Registered/remote binding kind mismatch")
        if binding.portability != registration.portability:
            raise _fail("Binding portability mismatch")
        if expected_effect is not None and expected_effect != registration.effect:
            raise _fail("Binding effect mismatch")
        if (binding.expected_contract_digest is not None
                and binding.expected_contract_digest != registration.contract_digest):
            raise _fail("Expected contract digest mismatch")
        if input_contract is not None:
            assertion = _schema(input_contract)
            if canonical_json(assertion) != canonical_json(registration.input_schema):
                raise _fail("Input contract assertion mismatch")
        if set(binding.config) - set(registration.allowed_config):
            raise _fail("Configuration key is not allowed")
        if set(binding.secret_refs) - set(registration.allowed_secret_refs):
            raise _fail("Secret HANDLE reference is not allowed")
        _validate(registration.config_schema, binding.config)
        entry = BindingEntry(binding, registration.contract_digest, registration.artifact_digest,
                             registration.effect, registration.abi, registration.input_schema,
                             registration.validator_contract_id)
        return ResolvedBinding(entry, registration)


@dataclass(frozen=True, slots=True)
class AppDependencyContract:
    dependency_contract_id: str
    build: str

    def __post_init__(self) -> None:
        validate_id(self.dependency_contract_id)
        _text(self.build)

    @classmethod
    def from_json(cls, value: object) -> AppDependencyContract:
        obj = _object(value, "dependency_contract_id build")
        return cls(validate_id(obj["dependency_contract_id"]), _text(obj["build"]))

    def as_json(self) -> dict[str, object]:
        return {"dependency_contract_id": self.dependency_contract_id, "build": self.build}


@dataclass(frozen=True, slots=True)
class ExecutionManifest:
    """Public manifest preimage, excluding manifest_digest when hashing.

    Ordered binding_entries/environment_compatibility are preserved, not silently
    sorted or deduplicated. Repeated entries are legal (multiple nodes can reuse
    a binding). This is execution identity, not a definition or generation UUID.
    Parsing verifies integrity but does NOT confer trusted registry authority.
    """

    compiler_contract_version: str
    compiler_build: str
    registry_digest: str
    binding_entries: tuple[BindingEntry, ...]
    app_dependency_contract: AppDependencyContract
    environment_compatibility: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.compiler_contract_version)
        _text(self.compiler_build)
        _digest(self.registry_digest)
        entries = tuple(self.binding_entries)
        if any(not isinstance(e, BindingEntry) for e in entries):
            raise _fail("Expected binding entries")
        if not isinstance(self.app_dependency_contract, AppDependencyContract):
            raise _fail("Expected an app dependency contract")
        object.__setattr__(self, "binding_entries", entries)
        object.__setattr__(self, "environment_compatibility", _ids(self.environment_compatibility))

    def _subject(self) -> dict[str, object]:
        return {"schema": "contexture.manifest/v1",
                "compiler_contract_version": self.compiler_contract_version,
                "compiler_build": self.compiler_build, "registry_digest": self.registry_digest,
                "binding_entries": [entry.as_json() for entry in self.binding_entries],
                "app_dependency_contract": self.app_dependency_contract.as_json(),
                "environment_compatibility": list(self.environment_compatibility)}

    @property
    def manifest_digest(self) -> str:
        return canonical_digest(self._subject(), "contexture.manifest/v1")

    def as_json(self) -> dict[str, object]:
        return {**self._subject(), "manifest_digest": self.manifest_digest}

    @classmethod
    def from_json(cls, value: object) -> ExecutionManifest:
        obj = _object(value, "schema compiler_contract_version compiler_build registry_digest "
                      "binding_entries app_dependency_contract environment_compatibility manifest_digest")
        if obj["schema"] != "contexture.manifest/v1":
            raise _fail("Unsupported execution manifest schema")
        raw_entries = obj["binding_entries"]
        if not isinstance(raw_entries, (list, tuple)):
            raise _fail("Expected binding entry array")
        result = cls(_text(obj["compiler_contract_version"]), _text(obj["compiler_build"]),
                     _digest(obj["registry_digest"]),
                     tuple(BindingEntry.from_json(e) for e in raw_entries),
                     AppDependencyContract.from_json(obj["app_dependency_contract"]),
                     _ids(obj["environment_compatibility"]))
        if _digest(obj["manifest_digest"]) != result.manifest_digest:
            raise _fail("Manifest digest mismatch")
        return result

    @classmethod
    def resolve(
        cls, registry: RegistryGeneration, bindings: Iterable[BindingDescriptor], *,
        compiler_contract_version: str, compiler_build: str,
        app_dependency_contract: AppDependencyContract,
        environment_compatibility: tuple[str, ...] = (),
    ) -> ExecutionManifest:
        """Pure explicit resolution, not graph lowering or resource preparation."""
        return cls(compiler_contract_version, compiler_build, registry.registry_digest,
                   tuple(registry.resolve(b).entry for b in bindings),
                   app_dependency_contract, environment_compatibility)