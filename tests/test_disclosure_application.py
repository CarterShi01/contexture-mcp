"""The disclosure-only application is isolated below the MCP surface."""

from __future__ import annotations

import asyncio
import unittest

from contexture import Channels, Contexture, Prompt, Resource, Role, Tool
from contexture.core.constants import DISCOVER_TOOL, OPEN_TOOL
from contexture.core.errors import ModelValidationError
from contexture.core.model.runtime import ApplicationRuntime
from contexture.core.model.system_api import DisclosureAPI, ExecutionAPI
from contexture.server import (
    DisclosureSurface,
    compile_application,
    compile_disclosure_application,
)


class RuntimeTool(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="runtime-tool", description="A runtime-only tool.", read_only=True
        )

    async def invoke(self) -> str:
        return "runtime"


class RuntimeRoot(Role):
    def __init__(self) -> None:
        super().__init__(
            name="runtime",
            description="Runtime-owned data.",
            instructions="Run the product.",
            tools=[RuntimeTool()],
        )


class ArchitectureFact(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="provider",
            description="Describe a provider relationship.",
            read_only=True,
        )

    async def invoke(self) -> str:  # must never acquire a binding
        raise AssertionError("a disclosure-only Tool cannot be invoked")


class ArchitectureRoot(Role):
    def __init__(self) -> None:
        super().__init__(
            name="architecture",
            description="Architecture-owned data.",
            instructions="Read the system structure before advising changes.",
            tools=[ArchitectureFact()],
        )


class ReviewArchitecture(Prompt):
    def __init__(self) -> None:
        super().__init__(
            opens="architecture",
            description="Review the architecture structure.",
        )


class RuntimeDocument(Resource):
    def __init__(self) -> None:
        super().__init__(
            opens="architecture/provider",
            uri="contexture://architecture/provider",
            description="Provider details.",
        )


class RuntimeChannels(Channels):
    pass


class DisclosureApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compiled = compile_disclosure_application(
            Contexture(
                name="architecture",
                roots=(ArchitectureRoot,),
                prompts=(ReviewArchitecture,),
            )
        )

    def test_compiles_an_unbound_index_and_no_runtime(self) -> None:
        self.assertFalse(self.compiled.index.is_bound)
        self.assertFalse(hasattr(self.compiled, "runtime"))
        with self.assertRaisesRegex(ModelValidationError, "bound Index"):
            ApplicationRuntime(self.compiled.index)
        with self.assertRaisesRegex(ModelValidationError, "no executable bindings"):
            self.compiled.index.binding_of("architecture/provider")
        with self.assertRaisesRegex(Exception, "bound Index"):
            ExecutionAPI(self.compiled.index)

    def test_tool_cards_are_structural_and_keep_progressive_disclosure(self) -> None:
        discovered = asyncio.run(self.compiled.server().surface.api.discover())
        self.assertEqual([card["name"] for card in discovered["roles"]], ["architecture"])
        self.assertNotIn("provider", str(discovered))

        opened = asyncio.run(self.compiled.server().surface.api.open("architecture"))
        card = opened["tools"][0]
        self.assertEqual(card["ref"], "architecture/provider")
        self.assertNotIn("input_schema", card)
        self.assertNotIn("read_only", card)

    def test_mcp_surface_has_only_navigation_tools_and_no_resources(self) -> None:
        server = self.compiled.server()
        self.assertIsInstance(server.surface, DisclosureSurface)
        wire = server.build()
        self.assertEqual(
            tuple(tool.name for tool in asyncio.run(wire.list_tools())),
            (DISCOVER_TOOL, OPEN_TOOL),
        )
        self.assertEqual(asyncio.run(wire.list_resources()), [])
        self.assertIn(
            "goto", tuple(prompt.name for prompt in asyncio.run(wire.list_prompts()))
        )

    def test_runtime_and_architecture_have_independent_data_and_indexes(self) -> None:
        runtime = compile_application(
            Contexture(name="runtime", roots=(RuntimeRoot,))
        )
        self.assertIsNot(runtime.index, self.compiled.index)
        self.assertIn("runtime/runtime-tool", runtime.index)
        self.assertNotIn("architecture", runtime.index)
        self.assertIn("architecture/provider", self.compiled.index)
        self.assertNotIn("runtime", self.compiled.index)

    def test_resources_and_channels_are_rejected_not_ignored(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "Resources"):
            compile_disclosure_application(
                Contexture(
                    name="architecture",
                    roots=(ArchitectureRoot,),
                    resources=(RuntimeDocument,),
                )
            )
        with self.assertRaisesRegex(ModelValidationError, "Channels"):
            compile_disclosure_application(
                Contexture(
                    name="architecture",
                    roots=(ArchitectureRoot,),
                    channels=RuntimeChannels,
                )
            )

    def test_runtime_surface_cannot_be_relabelled_as_disclosure_only(self) -> None:
        runtime = compile_application(Contexture(name="runtime", roots=(RuntimeRoot,)))
        with self.assertRaisesRegex(ModelValidationError, "unbound Index"):
            DisclosureSurface.of(runtime.disclosure)

    def test_disclosure_api_is_the_only_api_held_by_the_surface(self) -> None:
        self.assertIsInstance(self.compiled.server().surface.api, DisclosureAPI)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
