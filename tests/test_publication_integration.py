"""Publication equipment follows the existing runtime and MCP execution paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from contexture import (
    Channels,
    Contexture,
    Principal,
    Publication,
    Role,
    Tool,
    current_principal,
)
from contexture.core.constants import INVOKE_TOOL, OPEN_TOOL
from contexture.core.errors import ModelValidationError, WrongDoorError
from contexture.core.model.disclosure import Disclosure
from contexture.core.model.runtime import ApplicationRuntime
from contexture.core.model.system_api import GATEWAY_TOOLS, Refused, SystemAPI
from contexture.inspection import every_ref, open_step
from contexture.server import compile_disclosure_application

from tests.serving import compiled, serve


class Storage(Channels):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.opens = 0
        self.closes = 0

    async def open(self) -> None:
        self.opens += 1

    async def close(self) -> None:
        self.closes += 1


def storage_of(tool: Tool) -> Storage:
    if not isinstance(tool.channels, Storage):
        raise RuntimeError("Publication storage was not provisioned.")
    return tool.channels


class SaveFinding(Tool):
    def __init__(self) -> None:
        super().__init__(name="save", description="Save task findings.")

    async def invoke(self, finding: str) -> dict[str, str]:
        if not finding.strip():
            raise ValueError("A finding must not be empty.")
        target = storage_of(self).root / "findings.md"
        target.write_text(finding, encoding="utf-8")
        return {"status": "saved", "ref": target.name}


class ProposeKnowledge(Tool):
    def __init__(self) -> None:
        super().__init__(name="propose", description="Propose reusable knowledge.")

    async def invoke(self) -> dict[str, str]:
        root = storage_of(self).root
        finding = (root / "findings.md").read_text(encoding="utf-8")
        (root / "candidate.md").write_text(finding, encoding="utf-8")
        return {"status": "pending-review", "ref": "candidate.md"}


class ApproveKnowledge(Tool):
    def __init__(self) -> None:
        super().__init__(name="approve", description="Approve a knowledge candidate.")

    async def invoke(self) -> dict[str, str]:
        principal = current_principal()
        if principal is None or "knowledge.approve" not in principal.scopes:
            raise PermissionError("Knowledge approval requires an authorized reviewer.")
        root = storage_of(self).root
        candidate = (root / "candidate.md").read_text(encoding="utf-8")
        (root / "knowledge.md").write_text(candidate, encoding="utf-8")
        return {"status": "published", "ref": "knowledge.md"}


class TaskPublication(Publication):
    def __init__(self) -> None:
        super().__init__(
            name="memory",
            description="Preserve reusable task findings.",
            instructions="Save supported findings and report the receipt.",
            tools=[SaveFinding()],
        )


class KnowledgePublication(Publication):
    def __init__(self) -> None:
        super().__init__(
            name="knowledge",
            description="Promote reviewed project knowledge.",
            instructions=(
                "Propose only reusable findings after task storage succeeds. "
                "Await authorized review; a pending candidate is not published."
            ),
            tools=[ProposeKnowledge(), ApproveKnowledge()],
        )


class Worker(Role):
    def __init__(self) -> None:
        super().__init__(
            name="worker",
            description="Investigate a task.",
            instructions="Investigate and establish the evidence.",
            publication=TaskPublication(),
        )


@pytest.mark.asyncio
async def test_disclosure_and_inspection_have_no_publication_effects(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    tree = Disclosure(compiled(Worker, channels=storage))
    api = SystemAPI(tree)

    await api.discover()
    for ref in every_ref(tree):
        opened = await api.open(ref)
        assert open_step(tree, ref).payload == opened

    assert list(tmp_path.iterdir()) == []
    assert (storage.opens, storage.closes) == (0, 0)


@pytest.mark.asyncio
async def test_explicit_mcp_call_uses_disclosed_schema_and_persists(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    server = serve(Worker, channels=storage).build()
    assert tuple(tool.name for tool in await server.list_tools()) == GATEWAY_TOOLS

    owner_result = await server.call_tool(OPEN_TOOL, {"ref": "worker"})
    owner = json.loads(owner_result.content[0].text)
    publication_result = await server.call_tool(OPEN_TOOL, {"ref": owner["publication"]})
    publication = json.loads(publication_result.content[0].text)
    tool = publication["tools"][0]
    assert tool["ref"] == "worker/memory/save"
    assert tool["read_only"] is False
    assert tool["input_schema"]["required"] == ["finding"]
    assert tool["input_schema"]["properties"]["finding"]["type"] == "string"
    assert list(tmp_path.iterdir()) == []

    result = await server.call_tool(
        INVOKE_TOOL,
        {"ref": tool["ref"], "arguments": {"finding": "The reproducible evidence."}},
    )
    assert not result.is_error
    assert "saved" in result.content[0].text
    assert (tmp_path / "findings.md").read_text(encoding="utf-8") == (
        "The reproducible evidence."
    )


@pytest.mark.asyncio
async def test_publication_tools_keep_validation_errors_and_write_door(tmp_path: Path) -> None:
    index = compiled(Worker, channels=Storage(tmp_path))
    runtime = ApplicationRuntime(index)
    with pytest.raises(WrongDoorError):
        await runtime.invoke_read_only("worker/memory/save", {"finding": "evidence"})
    with pytest.raises(ToolError):
        await runtime.invoke("worker/memory/save", {})
    assert list(tmp_path.iterdir()) == []

    # A failing business call must not become a publication receipt.
    (tmp_path / "findings.md").mkdir()
    with pytest.raises(ToolError):
        await runtime.invoke("worker/memory/save", {"finding": "evidence"})
    assert (tmp_path / "findings.md").is_dir()


@pytest.mark.asyncio
async def test_composed_publication_preserves_pending_review_and_authority(tmp_path: Path) -> None:
    publication = Publication(
        name="results",
        description="Preserve task and project results.",
        instructions=(
            "First consolidate task findings. Then consider proposing reusable "
            "knowledge, respecting review and reporting pending approval."
        ),
        children=[TaskPublication(), KnowledgePublication()],
    )
    worker = Role(
        name="worker",
        description="Investigate a task.",
        instructions="Investigate.",
        publication=publication,
    )
    storage = Storage(tmp_path)
    index = compiled(worker, channels=storage)
    runtime = ApplicationRuntime(index)
    tree = Disclosure(index)
    async with index.provisioned():
        owner = tree.open("worker")
        result_roles = tree.open(owner["publication"])["roles"]
        assert [role["name"] for role in result_roles] == ["memory", "knowledge"]
        await runtime.invoke("worker/results/memory/save", {"finding": "Evidence."})
        assert not (tmp_path / "candidate.md").exists()
        candidate = await runtime.invoke("worker/results/knowledge/propose")
        assert candidate["status"] == "pending-review"
        assert not (tmp_path / "knowledge.md").exists()

        with pytest.raises(ToolError, match="authorized reviewer"):
            await runtime.invoke(
                "worker/results/knowledge/approve",
                principal=Principal(subject="worker"),
            )
        assert not (tmp_path / "knowledge.md").exists()
        receipt = await runtime.invoke(
            "worker/results/knowledge/approve",
            principal=Principal(
                subject="reviewer", scopes=frozenset({"knowledge.approve"})
            ),
        )
        assert receipt["status"] == "published"

    assert (storage.opens, storage.closes) == (1, 1)
    assert (tmp_path / "knowledge.md").read_text(encoding="utf-8") == "Evidence."
    assert current_principal() is None


@pytest.mark.asyncio
async def test_prompt_only_publication_cannot_be_entered_by_model(tmp_path: Path) -> None:
    tree = Disclosure(
        compiled(Worker, channels=Storage(tmp_path)),
        prompt_roots=frozenset({"worker"}),
    )
    api = SystemAPI(tree)
    assert await api.discover() == {"roles": [], "skills": [], "tools": []}
    for ref in ("worker", "worker/memory", "worker/memory/save"):
        with pytest.raises(Refused):
            await api.open(ref)
    with pytest.raises(Refused):
        await api.invoke("worker/memory/save", {"finding": "evidence"})
    assert list(tmp_path.iterdir()) == []
    owner = await api.open_for_person("worker")
    assert owner["publication"] == "worker/memory"


@pytest.mark.asyncio
async def test_disclosure_only_publication_has_no_executable_binding() -> None:
    app = compile_disclosure_application(
        Contexture(name="architecture", roots=(Worker,))
    )
    wire = app.server().build()
    assert tuple(tool.name for tool in await wire.list_tools()) == GATEWAY_TOOLS[:2]
    owner = await app.server().surface.api.open("worker")
    opened = await app.server().surface.api.open(owner["publication"])
    assert opened["tools"] == [
        {
            "kind": "tool",
            "name": "save",
            "description": "Save task findings.",
            "ref": "worker/memory/save",
        }
    ]
    with pytest.raises(ModelValidationError, match="no executable bindings"):
        app.index.binding_of("worker/memory/save")
