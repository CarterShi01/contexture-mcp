"""The business-facing Application declaration stays inert until compiled."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from pathlib import Path

from contexture import Contexture, Prompt, Role, Skill, Tool
from contexture.core.errors import ModelValidationError
from contexture.core.model.system_api import Refused
from contexture.server import build_server, compile_application
from contexture.server.instructions import build as build_instructions


SOURCE_ROOT = Path(__file__).resolve().parent.parent
_built = 0


class Hello(Tool):
    def __init__(self) -> None:
        global _built
        _built += 1
        super().__init__(name="hello", description="Say hello.", read_only=True)

    async def invoke(self) -> str:
        return "hello"


class Greeting(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="greeting",
            description="Greet a caller.",
            instructions="Call hello.",
            uses=("root/hello",),
        )


class Root(Role):
    def __init__(self) -> None:
        super().__init__(
            name="root",
            description="The root role.",
            instructions="Greet callers.",
            skills=[Greeting()],
            tools=[Hello()],
        )


class HumanCommand(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="do",
            description="Run the explicit user command.",
            instructions="Carry out the user-selected procedure.",
        )


class HiddenAction(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="hidden-action",
            description="Run a human-owned action.",
            read_only=True,
        )

    async def invoke(self) -> str:
        return "hidden"


class Commands(Role):
    def __init__(self) -> None:
        super().__init__(
            name="commands",
            description="User-controlled commands.",
            instructions="Only a person chooses this command tree.",
            skills=[HumanCommand()],
            tools=[HiddenAction()],
        )


class Do(Prompt):
    def __init__(self) -> None:
        super().__init__(
            opens="commands/do",
            name="do",
            description="Run the explicit user command.",
        )


class ApplicationTests(unittest.TestCase):
    def test_a_declaration_stores_factories_without_constructing_them(self) -> None:
        global _built
        _built = 0
        roots = [Root]

        app = Contexture(name=" hello ", roots=roots)

        self.assertEqual(_built, 0)
        self.assertEqual(app.name, "hello")
        self.assertEqual(app.roots, (Root,))
        roots.clear()
        self.assertEqual(app.roots, (Root,))

    def test_a_root_must_be_a_node_class(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "already-built"):
            Contexture(name="hello", roots=(Root(),))

        with self.assertRaisesRegex(ModelValidationError, "already-built"):
            Contexture(name="hello", roots=(Root,), prompt_roots=(Commands(),))

    def test_a_declaration_requires_a_name_and_a_root(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "non-empty"):
            Contexture(name=" ", roots=(Root,))
        with self.assertRaisesRegex(ModelValidationError, "at least one"):
            Contexture(name="hello", roots=())

    def test_importing_the_public_facade_and_declaring_an_app_loads_no_sdk(self) -> None:
        script = "\n".join(
            [
                "import sys",
                f"sys.path.insert(0, {str(SOURCE_ROOT)!r})",
                "from contexture import Contexture, Role",
                "class Root(Role):",
                "    def __init__(self):",
                "        super().__init__(name='root', description='Root.', instructions='Do work.')",
                "app = Contexture(name='hello', roots=(Root,))",
                "assert not any(name.split('.')[0] in {'mcp', 'mcp_types'} for name in sys.modules)",
            ]
        )
        subprocess.run([sys.executable, "-c", script], check=True)

    def test_each_compile_builds_a_fresh_forest_and_the_server_uses_it(self) -> None:
        global _built
        _built = 0
        app = Contexture(name="hello", roots=(Root,))

        first = compile_application(app)
        second = compile_application(app)

        self.assertIsNot(first.index.find("root"), second.index.find("root"))
        self.assertEqual(_built, 2)
        self.assertIs(build_server(app).index.roots[0].__class__, Root)

    def test_prompt_roots_share_one_index_but_not_model_navigation(self) -> None:
        app = Contexture(
            name="hello",
            roots=(Root,),
            prompt_roots=(Commands,),
            prompts=(Do,),
        )

        compiled = compile_application(app)
        server = compiled.server()
        discovered = asyncio.run(server.surface.api.discover())

        self.assertEqual([card["ref"] for card in discovered["roles"]], ["root"])
        self.assertIn("commands/do", compiled.index)
        self.assertNotIn("commands", build_instructions(compiled.disclosure))
        with self.assertRaises(Refused):
            asyncio.run(server.surface.api.open("commands"))
        with self.assertRaises(Refused):
            asyncio.run(server.surface.api.open("commands/do"))
        with self.assertRaises(Refused):
            asyncio.run(
                server.surface.api.invoke_read_only("commands/hidden-action")
            )

        prompt = asyncio.run(server.build().get_prompt("do", {}))
        self.assertIn(
            "Carry out the user-selected procedure.",
            prompt.messages[0].content.text,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
