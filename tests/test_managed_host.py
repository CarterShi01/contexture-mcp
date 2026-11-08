"""The supported composition root drives the whole dynamic-tree loop (M5)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
from types import ModuleType

import pytest

from contexture.core.graph.bindings import AppDependencyContract, RegistryGeneration
from contexture.core.graph.errors import GraphError
from contexture.server.managed_host import (
    CompilerProfile, ManagedGraphHost, SingleActorPolicy, python_tool,
)
from contexture.server.managed_journal import managed_schema

ROOT = Path(__file__).parents[1]


def example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("managed_example", ROOT / "examples/managed_dynamic_tree.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_example_edits_live_tree_with_history_and_hot_swap() -> None:
    module = example()
    with tempfile.TemporaryDirectory(prefix="contexture-host-") as temp:
        out = await module.main(Path(temp) / "store")
    assert out["v1"]["version"] == 1 and out["v2"]["version"] == 2
    assert out["protected_dependents"] == ["root"]  # locked root's agent view changed
    assert out["old_request_kept_generation"] and not out["old_request_sees_lookup"]
    assert out["new_tool"]["query"] == "dynamic"  # Tool created at runtime executes
    assert out["node_history"] == ["2", "3"] and out["diff"] == [("summarize", "/name")]
    assert out["after_revert"] == "summarize"
    assert out["head"] == out["reopened_head"] == "5"


@pytest.mark.asyncio
async def test_reopen_resumes_serving_and_empty_store_waits_for_bootstrap() -> None:
    module = example()
    profile = CompilerProfile(AppDependencyContract("example-app", "1"))
    with tempfile.TemporaryDirectory(prefix="contexture-host-") as temp:
        store = Path(temp) / "store"
        async with ManagedGraphHost.open(store, graph_id="assistant", policy=SingleActorPolicy(),
                                         registry=module.REGISTRY, profile=profile) as host:
            assert not host.initialized
            with pytest.raises(GraphError) as error:
                host.runtime  # noqa: B018 - serving needs an accepted graph
            assert error.value.code == "not_ready"
            host.bootstrap(module.seed())
            await host.publish()
        async with ManagedGraphHost.open(store, graph_id="assistant", policy=SingleActorPolicy(),
                                         registry=module.REGISTRY, profile=profile) as host:
            await host.wait_ready(timeout=5)
            assert host.runtime.graph_id == "assistant"
            assert (await module.call(host, "search", query="x"))["version"] == 1


def test_python_tool_contract_identity_and_packaged_schema() -> None:
    async def handler(arguments, context, resource):
        return arguments

    first = python_tool("echo", handler, artifact="pkg@1")
    assert first.argument_free and first.abi == "python-managed-call/v1"
    assert python_tool("echo", handler, artifact="pkg@2").contract_digest != first.contract_digest
    required = python_tool("echo", handler, artifact="pkg@1",
                           input_schema={"type": "object", "required": ["x"]})
    assert not required.argument_free
    RegistryGeneration(build="b", registrations=[first])
    spec = (ROOT / "spec/managed/schemas/managed.schema.json").read_bytes()
    packaged = (ROOT / "contexture/server/managed.schema.json").read_bytes()
    assert spec == packaged, "packaged schema must be byte-identical to the spec"
    assert managed_schema()["$defs"]["PreviewReceipt"]
