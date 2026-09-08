"""Tests for the two planes of text that are still `server`'s.

What one call answers with, refusals included, moved to the kernel with the
calls themselves; `tests/test_system_api.py` holds it. What is left here is the
text shaped by its audience rather than by the model — the opening a host loads
before calling anything, and what a person reads in a command menu.

These run without the MCP SDK installed, and that is the point: what a host is
told is decided a layer below the wire, and it should be readable, assertable
and reviewable on its own.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from contexture.core.mcp_interface import tool as primitive
from contexture.server import messages

SOURCE_ROOT = Path(__file__).resolve().parent.parent


class ToolPrimitiveTests(unittest.TestCase):
    def test_the_business_adds_nothing_to_this_plane(self) -> None:
        """Five names, whatever the declaration contains.

        The other two primitives grow an entry per declared `Prompt` or
        `Resource`. This one cannot: a listed capability is one every session
        pays for forever, so the plane is the framework's own and a business
        extends it by extending nothing — a rule the type now carries, since
        `ToolPlane` refuses to be subclassed.
        """

        self.assertEqual(len(primitive.TOOLS.names), 5)
        self.assertEqual(primitive.TOOLS.names[0], primitive.DISCOVER_TOOL)

        with self.assertRaises(TypeError):

            class Extended(primitive.ToolPlane):  # type: ignore[misc]
                pass

    def test_the_names_here_are_the_names_that_are_implemented(self) -> None:
        """Two modules, one list, and no way for them to drift apart.

        Which names occupy the plane is declared here; what they do is in
        `core.model.system_api`. Both take the strings from the shared ground,
        so this asserts that neither has grown a fifth of its own.
        """

        from contexture.core.model.system_api import GATEWAY_TOOLS

        self.assertEqual(set(primitive.TOOLS.names), set(GATEWAY_TOOLS))


class PreambleTests(unittest.TestCase):
    def test_the_opening_fits_the_budget_codex_reads(self) -> None:
        """Codex decides whether to use the server from the first 512 chars."""

        self.assertLessEqual(len(messages.PREAMBLE), 512)

    def test_the_opening_names_the_tools_it_tells_the_agent_to_call(self) -> None:
        for name in (
            primitive.OPEN_TOOL,
            primitive.INVOKE_TOOL,
            primitive.INVOKE_READ_ONLY_TOOL,
        ):
            with self.subTest(tool=name):
                self.assertIn(name, messages.PREAMBLE)


class IndependenceTests(unittest.TestCase):
    def test_deciding_what_the_agent_reads_needs_no_wire(self) -> None:
        """Importing the contract must not drag in the SDK.

        The three modules of this layer change at three different rates. This
        one is the slowest, and it stays testable on its own.

        Asked in a subprocess, because `sys.modules` belongs to the process and
        not to this test: run after any module that imports the SDK — which is
        most of this suite — an in-process check reports the whole suite's
        imports and fails on work this module did not do. It passed alone and
        failed in the run, which is the reading that matters.
        """

        script = (
            "import sys; sys.path.insert(0, %r);"
            "import contexture.server.messages;"
            "print(','.join(sorted(m for m in sys.modules "
            "if m == 'mcp' or m.startswith('mcp.'))))" % str(SOURCE_ROOT)
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
