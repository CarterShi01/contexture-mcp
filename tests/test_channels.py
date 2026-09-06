"""What a capability reaches outside its own process, and how it got there.

A Tool is built by the declaration machinery and shared by every call in the
process, so it cannot hold a connection — it has to be handed one. These tests
check the whole path that hand-off travels: an application builds its
connections, registers its controllers against them, and a capability reached
through the wire finds the object the application built.

The in-process cases pin the mechanism. The stdio case pins the claim, because
a resource read and a tool call arrive through different SDK machinery and only
a real client exercises both.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
import os
import sys
import tempfile
import unittest
from pathlib import Path

from contexture import Channels
from contexture.core.model.index import Index
from contexture.core.model.manager import ControllerManager

# The suite is discovered with the project root as its top level, so a test
# module is imported under its bare name and a sibling fixture is not a
# package member. Both runners reach it the same way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import channels_fixture as fixture  # noqa: E402

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:  # pragma: no cover - the SDK is a hard dependency
    ClientSession = None  # type: ignore[assignment]

SOURCE_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_MODULE = "tests.channels_fixture"
TIMEOUT_SECONDS = 30


def _run(work, *, marks: Path | None = None):
    async def session() -> object:
        environment = dict(os.environ)
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            f"{SOURCE_ROOT}{os.pathsep}{existing}" if existing else str(SOURCE_ROOT)
        )
        if marks is not None:
            environment["LIFECYCLE_MARKS"] = str(marks)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", FIXTURE_MODULE],
            env=environment,
        )
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with stdio_client(parameters, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.discover()
                    return await work(client)

    return asyncio.run(asyncio.wait_for(session(), TIMEOUT_SECONDS))


def _text(result) -> str:
    return "".join(
        block.text for block in result.content if getattr(block, "type", "") == "text"
    )


def _index(server) -> Index:
    """The compiled index behind what a server serves.

    Reached rather than held: `ContextureServer` takes a sealed assembly, and
    the index is what it was sealed over — there is no second copy.
    """

    return server.surface.tree.index


def _node(reg, *segments):
    """One stamped node, from an index or from a manager not yet compiled."""

    index = reg if isinstance(reg, Index) else Index.of(reg)
    return index.find("/".join(segments))


class HandOffTests(unittest.TestCase):
    """The mechanism, in process."""

    def test_a_capability_reaches_what_the_application_built(self) -> None:
        app = fixture.build()
        tool = _node(_index(app), "operations", "escalation", "notify_squad")
        self.assertIs(tool.channels, _index(app).channels)

        answer = asyncio.run(tool.invoke(squad="payments"))
        self.assertEqual(answer, "https://gateway.internal:notify:payments#1")

    def test_it_knows_its_own_address_too(self) -> None:
        """The other thing a shared instance cannot work out for itself."""

        app = fixture.build()
        tool = _node(_index(app), "operations", "escalation", "where_am_i")
        self.assertEqual(
            asyncio.run(tool.invoke()),
            "operations/escalation/where_am_i -> https://gateway.internal",
        )

    def test_two_apps_in_one_process_keep_their_own_connections(self) -> None:
        left, right = fixture.build(), fixture.build()
        _index(left).channels.gateway.endpoint = "https://left.internal"
        _index(right).channels.gateway.endpoint = "https://right.internal"

        left_tool = _node(_index(left), "operations", "escalation", "where_am_i")
        right_tool = _node(_index(right), "operations", "escalation", "where_am_i")

        self.assertIn("left.internal", asyncio.run(left_tool.invoke()))
        self.assertIn("right.internal", asyncio.run(right_tool.invoke()))

    def test_a_served_graph_carries_the_handle_the_application_built(self) -> None:
        """One door now: the registry is where a handle is handed over.

        `ContextureServer` takes a sealed assembly and has no `channels`
        parameter of its own, so there is no second place for this answer to
        come from and no way for two of them to disagree.
        """

        channels = fixture.Downstream(
            gateway=fixture.Gateway(endpoint="https://short.internal"),
            catalogue={"runbook": "-"},
        )
        server = fixture.build(channels=channels)
        index = server.surface.tree.index
        tool = _node(index, "operations", "escalation", "notify_squad")

        self.assertIs(tool.channels, channels)
        self.assertFalse(hasattr(server, "channels"))

    def test_nothing_outside_the_process_is_the_ordinary_case(self) -> None:
        manager = ControllerManager()
        manager.register_role(fixture.Operations)
        self.assertIsNone(
            _node(manager, "operations", "escalation", "notify_squad").channels
        )


class LifecycleTests(unittest.TestCase):
    """Opening a handle that cannot simply be constructed, and closing it."""

    class Marked(Channels):
        """A handle whose every step is observable, and composable.

        Two resources rather than one, because the property worth pinning is
        the one `Channels.enter` exists for: they close in reverse, and the
        first is closed if the second fails to open.
        """

        def __init__(self, marks: list[str], *, fail: bool = False) -> None:
            self.marks = marks
            self.fail = fail
            self.gateway = None

        @asynccontextmanager
        async def _resource(self, name: str, *, boom: bool = False):
            self.marks.append(f"open {name}")
            if boom:
                raise RuntimeError("gateway unreachable")
            try:
                yield fixture.Downstream(
                    gateway=fixture.Gateway(endpoint="https://opened.internal"),
                    catalogue={"runbook": "-"},
                )
            finally:
                self.marks.append(f"close {name}")

        async def open(self) -> None:
            first = await self.enter(self._resource("a"))
            await self.enter(self._resource("b", boom=self.fail))
            self.gateway = first.gateway

        async def close(self) -> None:
            self.marks.append("close()")
            self.gateway = None

    def _manager(self, marks: list[str], *, fail: bool = False) -> ControllerManager:
        manager = ControllerManager(channels=self.Marked(marks, fail=fail))
        manager.register_role(fixture.Operations)
        return manager

    def test_a_handle_is_open_only_while_it_is_being_served(self) -> None:
        marks: list[str] = []
        manager = self._manager(marks)
        tool = _node(manager, "operations", "escalation", "notify_squad")

        # Stamped from the start, and the same object throughout: what changes
        # between here and inside `provisioned` is what it *holds*.
        self.assertIs(tool.channels, manager.channels)
        self.assertIsNone(tool.channels.gateway)

        async def serve() -> str:
            async with manager.provisioned():
                marks.append("serving")
                return tool.channels.gateway.endpoint

        self.assertEqual(asyncio.run(serve()), "https://opened.internal")
        self.assertEqual(
            marks,
            ["open a", "open b", "serving", "close()", "close b", "close a"],
        )
        # Cleared by the handle's own `close`, which is where it belongs: the
        # framework never learns what this object holds, so it cannot clear it.
        self.assertIsNone(tool.channels.gateway)

    def test_what_was_entered_is_unwound_in_reverse(self) -> None:
        """`async with a, b` semantics, recovered without a factory."""

        marks: list[str] = []

        async def serve() -> None:
            async with self._manager(marks).provisioned():
                pass

        asyncio.run(serve())
        self.assertEqual(
            marks, ["open a", "open b", "close()", "close b", "close a"]
        )

    def test_the_handle_is_closed_even_when_serving_raises(self) -> None:
        marks: list[str] = []
        manager = self._manager(marks)

        async def serve() -> None:
            async with manager.provisioned():
                raise RuntimeError("a request blew up")

        with self.assertRaises(RuntimeError):
            asyncio.run(serve())
        self.assertEqual(
            marks, ["open a", "open b", "close()", "close b", "close a"]
        )

    def test_a_handle_that_cannot_be_opened_stops_the_server_starting(self) -> None:
        marks: list[str] = []
        manager = self._manager(marks, fail=True)

        async def serve() -> None:
            async with manager.provisioned():
                marks.append("serving")

        with self.assertRaises(RuntimeError) as caught:
            asyncio.run(serve())

        self.assertIn("gateway unreachable", str(caught.exception))
        self.assertNotIn("serving", marks)
        # And the half that *did* open is closed on the way out, which is the
        # bug a hand-written `open`/`close` pair invites and this does not.
        self.assertEqual(marks, ["open a", "open b", "close a"])

    def test_it_may_be_opened_again(self) -> None:
        """A fresh exit stack per serving, so a second run still works.

        This used to be a run-time refusal — "pass a factory, never a context
        manager, because one is consumed by being entered" — because no type
        could say it. The type says it now.
        """

        marks: list[str] = []
        manager = self._manager(marks)

        async def twice() -> None:
            for _ in range(2):
                async with manager.provisioned():
                    pass

        asyncio.run(twice())
        self.assertEqual(marks.count("open a"), 2)
        self.assertEqual(marks.count("close a"), 2)

    def test_enter_outside_open_is_refused(self) -> None:
        """Anything entered outside a serving would never be closed."""

        with self.assertRaises(RuntimeError) as caught:
            asyncio.run(self.Marked([]).enter(object()))

        self.assertIn("outside open()", str(caught.exception))

    def test_a_handle_with_no_lifecycle_still_answers(self) -> None:
        """One shape for both kinds, so a caller need not ask which it holds."""

        channels = object()
        manager = ControllerManager(channels=channels)
        manager.register_role(fixture.Operations)

        async def serve() -> object:
            async with manager.provisioned() as opened:
                return opened

        self.assertIs(asyncio.run(serve()), channels)


@unittest.skipIf(ClientSession is None, "the MCP SDK is not installed")
class OverTheWireTests(unittest.TestCase):
    """The claim, through a real client and a real subprocess.

    A tool call and a resource read reach a capability through different SDK
    machinery — one carries a request context, the other is a bare function the
    SDK calls with no arguments at all. A hand-off that travelled with the
    request would work for one and not the other, and only this test can tell
    the difference.
    """

    def test_a_tool_call_reaches_the_gateway(self) -> None:
        async def work(client):
            return await client.call_tool(
                "contexture_invoke",
                {
                    "ref": "operations/escalation/notify_squad",
                    "arguments": {"squad": "payments"},
                },
            )

        self.assertIn("https://gateway.internal:notify:payments", _text(_run(work)))

    def test_a_resource_read_reaches_the_same_object(self) -> None:
        async def work(client):
            return await client.read_resource("contexture://runbooks/escalation")

        contents = _run(work).contents
        self.assertIn("Page the owner", contents[0].text)

    def test_an_opened_card_still_hides_the_hand_off(self) -> None:
        """`channels` is framework plumbing, not part of what a tool accepts.

        It is stamped onto the node rather than passed as a parameter, so it
        cannot reach a schema — but a card is what an agent calls against, so
        the claim is checked where an agent would meet it.
        """

        async def work(client):
            return await client.call_tool(
                "contexture_open", {"ref": "operations/escalation"}
            )

        payload = json.loads(_text(_run(work)))
        for card in payload["tools"]:
            self.assertNotIn("channels", json.dumps(card))
            self.assertNotIn("path", card["input_schema"].get("properties", {}))


@unittest.skipIf(ClientSession is None, "the MCP SDK is not installed")
class LifecycleOverTheWireTests(unittest.TestCase):
    """Opening and closing, observed from outside the process that does it.

    In-process cases can show that `provisioned()` opens and closes. Only a
    subprocess can show *when* — that opening happened before the server was
    able to answer anything, and that closing happened at all rather than being
    skipped by a process that simply exited.
    """

    def test_it_opens_before_serving_and_closes_after(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marks = Path(directory) / "marks.txt"

            async def work(client):
                return await client.call_tool(
                    "contexture_invoke",
                    {
                        "ref": "operations/escalation/notify_squad",
                        "arguments": {"squad": "payments"},
                    },
                )

            answer = _text(_run(work, marks=marks))
            steps = marks.read_text(encoding="utf-8").split()

        # The call reached a gateway that only exists between these two marks.
        self.assertIn("https://gateway.internal:notify:payments", answer)
        self.assertEqual(steps, ["open", "close"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
