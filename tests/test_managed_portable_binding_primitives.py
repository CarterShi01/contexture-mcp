"""Candidate data semantics through captured native callables, not ABI portability."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
from typing import cast

from contexture.application.managed_runtime import PythonManagedCallAdapter
from contexture.core.graph.bindings import BindingRegistration, RegistryGeneration, Schema, _validate
from contexture.core.graph.canonical import JSON, canonical_json, freeze_json, thaw_json
from contexture.core.graph.errors import GraphError
from contexture.core.graph.model import BindingDescriptor, Portability
from contexture.core.graph.policy import VerifiedContext

CORPUS = Path(__file__).resolve().parents[1] / "spec/managed/referencevectors/portable-binding-primitives.json"


def test_portable_binding_primitive_observations() -> None:
    corpus = json.loads(CORPUS.read_bytes())
    assert corpus["schema"] == "contexture.portable-binding-primitives/candidate-v1"
    stages: list[str] = []
    retained: list[dict[str, JSON]] = []
    resource = object()
    context = VerifiedContext("actor", "tenant", "1", frozenset())

    def decoder(arguments: object) -> dict[str, JSON]:
        stages.append("decoder")
        if not isinstance(arguments, dict):
            raise GraphError("schema_invalid", "Expected object")
        count = arguments.get("count", 2)
        if isinstance(count, str):
            if not re.fullmatch(r"(?:0|[1-9][0-9]?|-[1-9][0-9]?)", count):
                raise GraphError("schema_invalid", "Noncanonical bounded decimal")
            count = int(count)
        elif type(count) is float and count.is_integer():
            count = int(count)
        return {"count": count, "label": arguments.get("label", "default")}

    def validator(decoded: dict[str, JSON]) -> None:
        stages.append("validator")
        count = decoded["count"]
        if type(count) is not int or not 0 <= count <= 9:
            raise GraphError("schema_invalid", "Count outside native contract")

    async def handler(decoded: dict[str, JSON], verified: VerifiedContext, handle: object) -> object:
        stages.append("handler")
        assert handle is resource and verified is context
        result = dict(decoded, actor=verified.actor_id)
        retained.append(result)
        return result

    portability = Portability.from_json({"kind": "portable", "contract_id": corpus["contract_id"]})
    registration = BindingRegistration(
        binding_id="primitives", version="1", kind="registered", portability=portability,
        effect="read_only", abi="python-managed-call/v1", artifact_digest="sha256:" + "a" * 64,
        input_schema=cast(Schema, freeze_json(corpus["input_schema"])),
        validator_contract_id="count-range/v1", decoder_contract_id="decimal-count-label/v1",
        default_contract_id="count-two-label-default/v1", coercion_contract_id="bounded-decimal/v1",
        decoder=decoder, validator=validator, handler=handler,
    )
    binding = RegistryGeneration(build="candidate-1", registrations=[registration]).resolve(
        BindingDescriptor.from_json({"kind": "registered", "binding_id": "primitives",
                                    "version": "1", "portability": portability.as_json()}))
    adapter = PythonManagedCallAdapter()
    observations = []
    for case in corpus["cases"]:
        stages.clear()
        arguments = case["input"]
        before = canonical_json(arguments)
        try:
            frozen = freeze_json(arguments)
            # ManagedRuntime owns schema preflight; the Python ABI owns native dispatch.
            _validate(binding.entry.input_schema, frozen)
            result = asyncio.run(adapter.call(binding, frozen, context, resource))
            actual = {"status": "ok", "stages": list(stages), "output": thaw_json(result)}
            retained[-1]["label"] = "mutated-after-call"
            assert result == actual["output"]
        except GraphError:
            actual = {"status": "rejected", "stages": list(stages)}
        assert canonical_json(arguments) == before, case["id"]
        assert actual == case["expected"], case["id"]
        observations.append({"id": case["id"], "observation": actual})
    output = os.environ.get("CONTEXTURE_BINDING_OBSERVATIONS")
    if output:
        Path(output).write_bytes(canonical_json(observations))
