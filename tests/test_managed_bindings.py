"""Bounded WP07 registry/manifest evidence; no resource preparation claims."""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import rfc8785
from jsonschema import Draft202012Validator

from contexture.core.graph.bindings import (
    AppDependencyContract, BindingEntry, BindingRegistration, ExecutionManifest, RegistryGeneration,
)
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import BindingDescriptor, GraphDefinition, Portability

ARTIFACT = "sha256:" + "a" * 64
PORTABLE = {"kind": "portable", "contract_id": "primitive-v1"}
INPUT = {"type": "object", "properties": {}, "additionalProperties": False}


def forbidden(*args: object, **kwargs: object) -> object:
    raise AssertionError("Pure resolution executed trusted business code")


def registration(**changes: Any) -> BindingRegistration:
    fields: dict[str, Any] = dict(
        binding_id="reader", version="1", kind="registered",
        portability=Portability.from_json(PORTABLE), effect="read_only", abi="python-call-v1",
        artifact_digest=ARTIFACT, input_schema=deepcopy(INPUT), validator_contract_id="validator-v1",
        decoder_contract_id="decoder-v1", default_contract_id="defaults-none-v1",
        coercion_contract_id="coercion-none-v1", handler=forbidden, decoder=forbidden,
        validator=forbidden, factory=forbidden, argument_free=True,
        allowed_config=("limit",), config_schema={"type": "object", "properties": {
            "limit": {"type": "integer", "minimum": 1}}, "additionalProperties": False},
        allowed_secret_refs=("vault:reader",), resource_policy={"sharing": ["generation"]})
    return BindingRegistration(**(fields | changes))


def descriptor(**changes: Any) -> BindingDescriptor:
    return BindingDescriptor.from_json(dict(kind="registered", binding_id="reader", version="1",
                                             portability=PORTABLE) | changes)


def registry(*entries: BindingRegistration, build: str = "registry-build-1") -> RegistryGeneration:
    return RegistryGeneration(build=build, registrations=entries or [registration()])


def manifest(reg: RegistryGeneration | None = None, **binding: Any) -> ExecutionManifest:
    return ExecutionManifest.resolve(reg or registry(), [descriptor(**binding)],
        compiler_contract_version="compiler-v1", compiler_build="compiler-build-1",
        app_dependency_contract=AppDependencyContract("app-deps-v1", "app-build-1"),
        environment_compatibility=("python-3.11",))


def test_registration_and_resolution_do_not_execute_any_callable() -> None:
    entry = registration()
    result = registry(entry).resolve(descriptor(), input_contract=deepcopy(INPUT),
                                     expected_effect="read_only")
    assert result.registration is entry
    assert result.registration.handler is forbidden
    assert result.registration.decoder is forbidden
    assert result.registration.validator is forbidden
    assert result.registration.factory is forbidden
    assert result.entry.effect == "read_only"
    assert result.publication_contract.argument_free is True
    assert manifest(registry(entry)).binding_entries[0] == result.entry


def test_caller_mutation_cannot_change_pinned_contract_or_descriptor() -> None:
    schema = deepcopy(INPUT)
    config_schema = {"type": "object", "properties": {"limit": {"enum": [1, 2]}}}
    policy = {"cache": {"keys": ["limit"]}}
    allowed = ["limit"]
    secrets = ["vault:reader"]
    reg = registration(input_schema=schema, config_schema=config_schema,
                       resource_policy=policy, allowed_config=allowed, allowed_secret_refs=secrets)
    registrations = [reg]
    generation = RegistryGeneration(build="build", registrations=registrations)
    config = {"limit": 1}
    desc = descriptor(config=config, secret_refs=secrets)
    before = manifest(generation, config=config, secret_refs=secrets).as_json()
    schema["properties"]["evil"] = {"type": "string"}
    config_schema["properties"]["limit"]["enum"].append(99)
    policy["cache"]["keys"].append("evil")
    allowed.append("evil")
    config["limit"] = 99
    secrets.append("vault:evil")
    registrations.clear()
    assert len(generation.registrations) == 1
    assert generation.resolve(desc).entry.binding.config["limit"] == 1
    assert reg.input_schema["properties"] == {}
    assert reg.allowed_config == ("limit",)
    assert reg.allowed_secret_refs == ("vault:reader",)
    assert manifest(generation, config={"limit": 1}, secret_refs=["vault:reader"]).as_json() == before
    with pytest.raises(GraphError):
        generation.resolve(descriptor(config={"limit": 99}))
    with pytest.raises(TypeError):
        reg.resource_policy["new"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        generation.registrations[("reader", "2")] = reg  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        generation.build = "other"  # type: ignore[misc]


def test_exact_version_lookup_and_duplicate_keys() -> None:
    one, two = registration(), registration(version="2", artifact_digest="sha256:" + "b" * 64)
    generation = registry(two, one)
    assert generation.lookup("reader", "1") is one
    assert generation.lookup("reader", "2") is two
    for key, version in [("absent", "1"), ("reader", "3"), ("reader", "latest")]:
        with pytest.raises(GraphError, match="exact binding") as error:
            generation.lookup(key, version)
        assert error.value.code == "binding_not_found"
    with pytest.raises(GraphError, match="Duplicate"):
        registry(one, one)
    assert registry(one, two).registry_digest == generation.registry_digest


@pytest.mark.parametrize("changes", [
    {"kind": "remote"}, {"portability": {"kind": "portable", "contract_id": "other"}},
    {"portability": {"kind": "native", "language": "python", "version": "3.11", "factory": "f"}},
    {"expected_contract_digest": "sha256:" + "0" * 64},
    {"config": {"evil": 1}}, {"config": {"limit": 0}}, {"config": {"limit": "1"}},
    {"secret_refs": ["vault:unknown"]},
])
def test_mismatches_rejected_before_business_code(changes: dict[str, Any]) -> None:
    with pytest.raises(GraphError):
        registry().resolve(descriptor(**changes))


def test_effect_and_input_assertions_are_exact_not_authoritative() -> None:
    generation = registry()
    for effect in ("write", "unknown"):
        with pytest.raises(GraphError, match="effect mismatch"):
            generation.resolve(descriptor(), expected_effect=effect)  # type: ignore[arg-type]
    for assertion in (True, False, {}, {"type": "object"}):
        with pytest.raises(GraphError, match="assertion mismatch"):
            generation.resolve(descriptor(), input_contract=assertion)
    assert generation.resolve(descriptor(), input_contract=INPUT).entry.input_schema == INPUT
    false_registry = registry(registration(input_schema=False, argument_free=False))
    assert false_registry.resolve(descriptor(), input_contract=False).entry.input_schema is False
    with pytest.raises(GraphError):
        false_registry.resolve(descriptor(), input_contract=True)
    contract = generation.lookup("reader", "1").contract_digest
    assert generation.resolve(descriptor(expected_contract_digest=contract)).entry.contract_digest == contract


def test_config_and_handle_allowlists_do_not_materialize_or_insert_defaults() -> None:
    generation = registry(registration(config_schema={"type": "object", "properties": {
        "limit": {"type": "integer", "default": 5}}, "additionalProperties": False}))
    assert generation.resolve(descriptor()).entry.binding.config == {}
    resolved = generation.resolve(descriptor(config={"limit": 2}, secret_refs=["vault:reader"]))
    assert resolved.entry.binding.as_json()["secret_refs"] == ["vault:reader"]
    assert resolved.entry.binding.config == {"limit": 2}
    # Schema permission alone does not grant top-level policy permission.
    with pytest.raises(GraphError, match="not allowed"):
        registry(registration(config_schema=True)).resolve(descriptor(config={"token": "sensitive"}))
    with pytest.raises(GraphError) as error:
        generation.resolve(descriptor(config={"limit": "sensitive"}))
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("field,value", [
    ("effect", "write"), ("abi", "other-abi"),
    ("artifact_digest", "sha256:" + "b" * 64),
    ("validator_contract_id", "validator-v2"), ("decoder_contract_id", "decoder-v2"),
    ("default_contract_id", "defaults-v2"), ("coercion_contract_id", "coercion-v2"),
    ("input_schema", {"type": "object"}), ("argument_free", False),
    ("config_schema", True), ("allowed_config", ("limit", "extra")),
    ("allowed_secret_refs", ("vault:reader", "vault:extra")),
    ("resource_policy", {"sharing": ["process"]}), ("factory", None),
])
def test_semantic_contract_changes_change_manifest(field: str, value: Any) -> None:
    old = registration()
    changed = replace(old, **{field: value})
    assert changed.contract_digest != old.contract_digest
    assert registry(changed).registry_digest != registry(old).registry_digest
    assert manifest(registry(changed)).manifest_digest != manifest(registry(old)).manifest_digest


def test_build_config_and_dependency_identities_change_manifest_not_definition() -> None:
    definition = descriptor().as_json()
    base = manifest()
    variants = [manifest(registry(build="registry-build-2")), manifest(config={"limit": 1}),
                manifest(secret_refs=["vault:reader"]),
                replace(base, compiler_build="compiler-build-2"),
                replace(base, compiler_contract_version="compiler-v2"),
                replace(base, app_dependency_contract=AppDependencyContract("app-deps-v2", "app-build-1")),
                replace(base, app_dependency_contract=AppDependencyContract("app-deps-v1", "app-build-2")),
                replace(base, environment_compatibility=("python-3.12",))]
    assert len({base.manifest_digest, *(v.manifest_digest for v in variants)}) == len(variants) + 1
    assert descriptor().as_json() == definition
    unused = registration(binding_id="unused")
    assert manifest(registry(registration(), unused)).manifest_digest != base.manifest_digest


def test_native_and_remote_are_explicit_pinned_registrations() -> None:
    native = Portability.from_json({"kind": "native", "language": "python",
                                   "version": "3.11", "factory": "module.make"})
    reg = registration(kind="remote", portability=native)
    generation = registry(reg)
    binding = descriptor(kind="remote", portability=native.as_json())
    assert generation.resolve(binding).registration is reg
    for field, value in [("language", "go"), ("version", "3.12"), ("factory", "module.other")]:
        with pytest.raises(GraphError, match="portability mismatch"):
            generation.resolve(descriptor(kind="remote", portability=native.as_json() | {field: value}))
    with pytest.raises(GraphError, match="kind mismatch"):
        generation.resolve(descriptor(portability=native.as_json()))


def test_wire_schema_independent_hash_roundtrip_and_detached_returns() -> None:
    value = manifest(config={"limit": 1})
    raw = value.as_json()
    schema = json.loads((Path(__file__).parents[1] / "spec/managed/schemas/managed.schema.json").read_text())
    for name, wire in [("ExecutionManifest", raw), ("BindingEntry", raw["binding_entries"][0])]:
        Draft202012Validator({"$ref": "#/$defs/" + name, "$defs": schema["$defs"]}).validate(wire)
    subject = {k: v for k, v in raw.items() if k != "manifest_digest"}
    expected = "sha256:" + hashlib.sha256(b"contexture.manifest/v1\0" + rfc8785.dumps(subject)).hexdigest()
    assert value.manifest_digest == expected
    assert ExecutionManifest.from_json(json.dumps(raw).encode()) == value
    raw["binding_entries"][0]["binding"]["config"]["limit"] = 99
    raw["binding_entries"][0]["input_schema"]["properties"]["evil"] = True
    raw["environment_compatibility"].append("evil")
    assert value.binding_entries[0].binding.config["limit"] == 1
    assert value.binding_entries[0].input_schema["properties"] == {}
    assert value.environment_compatibility == ("python-3.11",)
    assert value.manifest_digest == expected


@pytest.mark.parametrize("field,value", [
    ("schema", "contexture.manifest/v2"), ("manifest_digest", "sha256:" + "0" * 64),
    ("compiler_build", "tampered"), ("registry_digest", "wrong"),
    ("environment_compatibility", ["duplicate", "duplicate"]), ("binding_entries", {}),
    ("app_dependency_contract", {"dependency_contract_id": "x", "build": "b", "extra": 1}),
])
def test_manifest_strict_wire_rejection(field: str, value: Any) -> None:
    raw = manifest().as_json() | {field: value}
    with pytest.raises(GraphError):
        ExecutionManifest.from_json(raw)


def test_unknown_missing_and_duplicate_wire_fields_fail_closed() -> None:
    for parser, raw in [(ExecutionManifest.from_json, manifest().as_json()),
                        (BindingEntry.from_json, manifest().binding_entries[0].as_json()),
                        (AppDependencyContract.from_json, {"dependency_contract_id": "d", "build": "b"})]:
        with pytest.raises(GraphError):
            parser(raw | {"extra": 1})
        for key in raw:
            with pytest.raises(GraphError):
                parser({k: v for k, v in raw.items() if k != key})
    with pytest.raises(GraphError, match="Duplicate JSON"):
        ExecutionManifest.from_json(b'{"schema":1,"schema":2}')


@pytest.mark.parametrize("schema", [
    {"type": "nonsense"}, {"$ref": "https://example.invalid/schema"},
    {"$defs": {"x": {"$ref": "file:///etc/passwd"}}, "$ref": "#/$defs/x"},
    {"$ref": "#/$defs/missing"}, {"$ref": "#anchor"}, {"$dynamicRef": "#node"},
    {"$id": "https://example.invalid/schema"},
    {"$schema": "http://json-schema.org/draft-07/schema#"},
])
def test_invalid_or_nonlocal_schemas_rejected_without_fetch(schema: Any, monkeypatch: Any) -> None:
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    with pytest.raises(GraphError):
        registration(input_schema=schema, argument_free=False)
    with pytest.raises(GraphError):
        registration(config_schema=schema)


def test_local_schema_refs_and_ref_shaped_instance_data() -> None:
    schema = {"type": "object", "$defs": {"limit": {"type": "integer", "minimum": 1}},
              "properties": {"limit": {"$ref": "#/$defs/limit"}}}
    generation = registry(registration(config_schema=schema))
    assert generation.resolve(descriptor(config={"limit": 1})).entry.binding.config["limit"] == 1
    with pytest.raises(GraphError):
        generation.resolve(descriptor(config={"limit": 0}))
    assert registration(input_schema={"const": {"$ref": "https://data.invalid"}},
                        argument_free=False).input_schema["const"]["$ref"] == "https://data.invalid"


def test_publication_evidence_validates_real_graph_without_executing_handler() -> None:
    binding = descriptor()
    resolved = registry().resolve(binding)
    raw = {"schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": "g",
           "application_name": "app", "mode": "runtime", "nodes": [{"node_id": "tool",
           "kind": "tool", "name": "reader", "description": "Read", "binding": binding.as_json()}],
           "roots": [{"node_id": "tool", "plane": "model"}],
           "presentations": {"default_locale": "en", "catalogs": []}, "publications": [{
               "kind": "resource", "publication_id": "r", "name": "result", "uri": "test:result",
               "mime_type": "application/json", "opens_id": "tool"}]}
    definition = GraphDefinition.from_json(raw, publication_contracts={"tool": resolved.publication_contract})
    assert definition.nodes["tool"].binding == binding
    for reg in (registration(effect="write"), registration(argument_free=False)):
        evidence = registry(reg).resolve(binding).publication_contract
        with pytest.raises(GraphError, match="argument-free read-only"):
            GraphDefinition.from_json(raw, publication_contracts={"tool": evidence})
    raw["publications"] = [{"kind": "route", "publication_id": "route", "method": "POST",
                            "path": "/result", "opens_id": "tool"}]
    write = registry(registration(effect="write", argument_free=False)).resolve(binding)
    assert GraphDefinition.from_json(raw, publication_contracts={
        "tool": write.publication_contract}).publications[0].method == "POST"
    with pytest.raises(GraphError, match="effect mismatch"):
        GraphDefinition.from_json(raw, publication_contracts={"tool": resolved.publication_contract})


@pytest.mark.parametrize("changes", [
    {"version": ""}, {"kind": "dynamic"}, {"effect": "unknown"}, {"abi": ""},
    {"artifact_digest": "sha256:" + "a" * 64 + "\n"}, {"decoder_contract_id": ""},
    {"default_contract_id": ""}, {"coercion_contract_id": ""},
    {"handler": None}, {"decoder": None}, {"validator": None}, {"factory": 1},
    {"allowed_config": ("limit", "limit")}, {"allowed_secret_refs": ("x", "x")},
    {"resource_policy": []}, {"argument_free": 1},
    {"input_schema": False}, {"input_schema": {"type": "object", "required": ["x"]}},
])
def test_invalid_trusted_registration_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(GraphError):
        registration(**changes)


def test_referenced_data_becomes_schema_and_cannot_hide_remote_ref() -> None:
    schema = {"default": {"$ref": "https://example.invalid/hidden"}, "$ref": "#/default"}
    with pytest.raises(GraphError, match="local JSON-pointer"):
        registration(input_schema=schema, argument_free=False)
    with pytest.raises(GraphError):
        registration(input_schema={"default": 5, "$ref": "#/default"}, argument_free=False)


def test_default_config_policy_permits_empty_config_only() -> None:
    reg = registration()
    # Exercise the actual defaults rather than the test helper's explicit policy.
    defaults = BindingRegistration(**{name: getattr(reg, name) for name in (
        "binding_id", "version", "kind", "portability", "effect", "abi", "artifact_digest",
        "input_schema", "validator_contract_id", "decoder_contract_id", "default_contract_id",
        "coercion_contract_id", "handler", "decoder", "validator")})
    assert registry(defaults).resolve(descriptor()).entry.binding.config == {}
    with pytest.raises(GraphError):
        registry(defaults).resolve(descriptor(config={"limit": 1}))


def test_manifest_captures_caller_owned_entry_and_environment_lists() -> None:
    base = manifest()
    entries = list(base.binding_entries)
    environments = list(base.environment_compatibility)
    captured = replace(base, binding_entries=entries, environment_compatibility=environments)
    digest = captured.manifest_digest
    entries.clear()
    environments.clear()
    assert len(captured.binding_entries) == 1
    assert captured.environment_compatibility == ("python-3.11",)
    assert captured.manifest_digest == digest


def test_pure_core_module_has_no_io_or_legacy_catalog_dependencies() -> None:
    path = Path(__file__).parents[1] / "contexture/core/graph/bindings.py"
    tree = ast.parse(path.read_text())
    imports = [name for node in ast.walk(tree) for name in (
        [alias.name for alias in node.names] if isinstance(node, ast.Import)
        else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])]
    forbidden_roots = {"os", "pathlib", "io", "sqlite3", "socket", "urllib", "http", "mcp",
                       "requests", "httpx", "subprocess"}
    assert not {name.split(".")[0] for name in imports} & forbidden_roots
    assert not any("catalog" in name or "introspection" in name for name in imports)