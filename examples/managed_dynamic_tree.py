"""Edit a live context tree, see its history, and hot-swap what agents get.

Run: python examples/managed_dynamic_tree.py [store-directory]

The store must be on local ext4/xfs/btrfs or Windows NTFS (strict durability)
and outside any git checkout. A temporary directory is used by default.

Steps:
 1. register deployed Python Tool code in a pinned registry
 2. seed a graph whose root is locked but has a mutable "skills" area
 3. publish revision 1 and call a Tool through a pinned request
 4. add a Skill and a NEW Tool (bound to deployed code) while a request is open
 5. publish: the open request keeps its generation; new requests see the edit
 6. read per-node history and a stable-ID diff; revert a rename
 7. upgrade a Tool's binding version in one transaction and publish
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

from contexture.core.graph.bindings import AppDependencyContract, RegistryGeneration
from contexture.core.graph.errors import GraphError
from contexture.server.managed_host import (
    CompilerProfile, ManagedGraphHost, SingleActorPolicy, python_tool, tool_binding,
)


async def search_v1(arguments: dict[str, Any], context: Any, resource: Any) -> dict[str, Any]:
    return {"version": 1, "query": arguments.get("query", ""), "hits": ["a", "b"]}


async def search_v2(arguments: dict[str, Any], context: Any, resource: Any) -> dict[str, Any]:
    return {"version": 2, "query": arguments.get("query", ""), "hits": ["a", "b", "c"]}


QUERY = {"type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False}
SEARCH_V1 = python_tool("search", search_v1, version="1", input_schema=QUERY, artifact="example:search@1")
SEARCH_V2 = python_tool("search", search_v2, version="2", input_schema=QUERY, artifact="example:search@2")
REGISTRY = RegistryGeneration(build="example-1", registrations=[SEARCH_V1, SEARCH_V2])


def seed() -> dict[str, Any]:
    def role(identity: str, name: str, **fields: Any) -> dict[str, Any]:
        return {"node_id": identity, "kind": "role", "name": name, "description": name.title(),
                "instructions": f"You are the {name}.", **fields}
    return {"schema": "contexture.graph/v1", "semantic_version": "1", "graph_id": "assistant",
        "application_name": "example", "mode": "runtime",
        "nodes": [
            # Root left at "inherit": locked. Only the explicit mutable area is editable.
            role("root", "assistant", members={"children": ["skills"]}),
            role("skills", "skills", mutable=True, members={"tools": ["search"]}),
            {"node_id": "search", "kind": "tool", "name": "search", "description": "Search docs",
             "binding": tool_binding(SEARCH_V1)},
        ],
        "roots": [{"node_id": "root", "plane": "model"}],
        "presentations": {"default_locale": "en", "catalogs": []}}


async def call(host: ManagedGraphHost, node_id: str, **arguments: Any) -> Any:
    async with host.runtime.request() as request:
        return await request.invoke_read_only(request.reference(node_id), arguments)


async def main(store: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    profile = CompilerProfile(AppDependencyContract("example-app", "1"))
    async with ManagedGraphHost.open(store, graph_id="assistant", policy=SingleActorPolicy(),
                                     registry=REGISTRY, profile=profile) as host:
        host.bootstrap(seed())
        await host.publish()
        out["v1"] = await call(host, "search", query="tree")

        # A request opened now stays pinned to generation 1 for its lifetime.
        async with host.runtime.request() as old_request:
            old_generation = old_request.generation_uuid
            preview = host.preview(
                {"op": "create_node", "node": {"node_id": "summarize", "kind": "skill",
                 "name": "summarize", "description": "Summarize results",
                 "instructions": "Summarize the hits.", "mutable": True}},
                {"op": "attach_node", "node_id": "summarize",
                 "target": {"parent_id": "skills", "slot": "skills", "position": 0}},
                # A NEW Tool node bound to already deployed code: no restart.
                {"op": "create_node", "node": {"node_id": "lookup", "kind": "tool", "name": "lookup",
                 "description": "Second door to search", "mutable": True,
                 "binding": tool_binding(SEARCH_V1)}},
                {"op": "attach_node", "node_id": "lookup",
                 "target": {"parent_id": "skills", "slot": "tools", "position": 1}},
                reason="add a Skill and a Tool")
            receipt = preview.as_json()
            out["preview_impact"] = receipt["impact"]
            out["protected_dependents"] = receipt["protected_dependents"]  # locked root's view changes
            host.owner.commit(preview)
            await host.publish()
            out["old_request_kept_generation"] = old_request.generation_uuid == old_generation
            try:
                await old_request.invoke_read_only(old_request.reference("lookup"), {})
                out["old_request_sees_lookup"] = True
            except GraphError:
                out["old_request_sees_lookup"] = False
        out["new_tool"] = await call(host, "lookup", query="dynamic")

        # Rename, inspect history, then revert just that node.
        renamed_at = host.change({"op": "rename_node", "node_id": "summarize", "new_name": "digest"},
                                 reason="rename")["revision"]
        out["node_history"] = [r.state.revision for r in host.node_history("summarize")]
        out["diff"] = [(e["id"], e["field"]) for e in
                       host.diff(str(int(renamed_at) - 1), renamed_at).entries("nodes")]
        host.revert(str(int(renamed_at) - 1), node_ids=["summarize"], reason="undo rename")
        out["after_revert"] = host.read().definition.nodes["summarize"].name

        # Upgrade the Tool's code version in ONE transaction, then serve it.
        host.change({"op": "update_node", "node_id": "search",
                     "set_fields": {"binding": tool_binding(SEARCH_V2)}, "unset_fields": []},
                    reason="search v2")
        await host.publish()
        out["v2"] = await call(host, "search", query="tree")
        out["head"] = host.read().revision

    # Reopen: replay needs only the journal, and serving resumes the desired release.
    async with ManagedGraphHost.open(store, graph_id="assistant", policy=SingleActorPolicy(),
                                     registry=RegistryGeneration(build="example-2",
                                                                 registrations=[SEARCH_V2]),
                                     profile=profile) as host:
        out["reopened_head"] = host.read().revision
    return out


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="contexture-example-") as temp:
        root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(temp) / "store"
        print(json.dumps(asyncio.run(main(root)), indent=2, default=str))
