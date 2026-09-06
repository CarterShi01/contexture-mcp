"""Tests for the projected surface: what reaches the wire, and what does not.

The surface is four tools whatever the declaration holds, business
capabilities never appear on it, and the read-only classification survives as
which entry point was used rather than as an argument a model could fill in.

The other two planes are here too, because what they must agree with is on
this one: a command answers with what `contexture_open` would have said, and a
resource names a node the tree already holds.
"""

from __future__ import annotations

import asyncio
import json
import re
import unittest

from mcp.server.mcpserver.exceptions import ToolError

from contexture.core.constants import (
    DISCOVER_TOOL,
    INVOKE_READ_ONLY_TOOL,
    INVOKE_TOOL,
    OPEN_TOOL,
)
from contexture.core.errors import ModelValidationError
from contexture.core.mcp_interface import Prompt, Resource
from contexture.core.model.disclosure import SEPARATOR, Disclosure
from contexture.core.model.role import Role
from contexture.core.model.skill import Skill
from contexture.core.model.system_api import GATEWAY_TOOLS
from contexture.core.model.tool import Tool
from mcp.server.mcpserver import Context, MCPServer
from contexture.server import instructions
from contexture.server.binding import TypeHintBinding
from contexture.server import (
    Launch,
    claude_code_config,
    cli_commands,
    codex_config,
)
from contexture.server.surface import Surface

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from serving import assemble, serve  # noqa: E402

PROCEDURE = "Read the status, then the logs."


class GetPodLogs(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="get_pod_logs",
            description="Return the recent container logs for a Pod.",
            read_only=True,
        )

    async def invoke(self, namespace: str, pod: str, previous: bool = False) -> str:
        return f"{namespace}/{pod} previous={previous}"


class DeletePod(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="delete_pod",
            description="Delete a Pod so its controller recreates it.",
            read_only=False,
        )

    async def invoke(self, namespace: str, pod: str) -> str:
        return f"deleted {namespace}/{pod}"


class Runbook(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="runbook",
            description="How to diagnose a container that keeps restarting.",
            read_only=True,
        )

    async def invoke(self) -> str:
        return "RUNBOOK-BODY"


#: What the fixture publishes on the resource primitive: one node the tree
#: already holds, at an address that does not move when the node does.
RUNBOOK_RESOURCE = Resource(
    opens="responder/runbook",
    uri="contexture://runbooks/crash-loop",
    mime_type="text/markdown",
    description="How to diagnose a container that keeps restarting.",
)


class Diagnose(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="diagnose",
            description="Find why a Pod restarts repeatedly.",
            instructions=PROCEDURE,
        )


class Responder(Role):
    def __init__(self) -> None:
        super().__init__(
            name="responder",
            description="Diagnose and repair unhealthy Pods.",
            instructions="Inspect before changing anything.",
            skills=[Diagnose()],
            tools=[GetPodLogs(), DeletePod(), Runbook()],
        )


def _server():
    return serve(Responder).build()


def _published():
    """The same graph, with the runbook published on the resource primitive."""

    return serve(Responder, published=(RUNBOOK_RESOURCE,)).build()


def _published_with(opens: str, uri: str):
    """Build a server publishing one node, for the refusals worth checking."""

    return serve(
        Responder,
        published=(
            Resource(
                opens=opens,
                uri=uri,
                mime_type="text/markdown",
                description="Whatever this names.",
            ),
        ),
    ).build()


def _call(server, name, arguments=None):
    return asyncio.run(server.call_tool(name, arguments or {}))


def _text(result):
    return result.content[0].text


class SurfaceTests(unittest.TestCase):
    def test_the_surface_is_the_gateway_tools_and_nothing_else(self) -> None:
        server = _server()

        listed = tuple(tool.name for tool in asyncio.run(server.list_tools()))
        self.assertEqual(listed, GATEWAY_TOOLS)

    def test_no_business_capability_reaches_the_surface(self) -> None:
        """A registered capability is one every session pays for, forever."""

        server = _server()

        rendered = json.dumps(
            [tool.model_dump(mode="json") for tool in asyncio.run(server.list_tools())]
        )
        self.assertNotIn("get_pod_logs", rendered)
        self.assertNotIn("delete_pod", rendered)
        self.assertNotIn(PROCEDURE, rendered)

        # Nothing is published on the resource primitive unless a declaration
        # asked for it, and this fixture's server is built without a surface.
        resources = asyncio.run(server.list_resources())
        self.assertEqual(resources, [])

    def test_only_the_writing_door_is_free_of_the_read_only_hint(self) -> None:
        server = _server()
        hints = {
            tool.name: tool.annotations and tool.annotations.read_only_hint
            for tool in asyncio.run(server.list_tools())
        }

        self.assertEqual(hints[INVOKE_TOOL], False)
        for name in (DISCOVER_TOOL, OPEN_TOOL, INVOKE_READ_ONLY_TOOL):
            with self.subTest(tool=name):
                self.assertTrue(hints[name])

    def test_read_only_is_never_an_argument(self) -> None:
        """A model that could pass its own approval flag would be approving
        its own writes."""

        server = _server()
        for tool in asyncio.run(server.list_tools()):
            with self.subTest(tool=tool.name):
                self.assertNotIn(
                    "read_only", tool.input_schema.get("properties", {})
                )

    def test_the_instructions_carry_the_role_roster(self) -> None:
        """A gateway whose five tool names all begin `contexture_` gives a host
        nothing to go on until the roster tells it what this server is for."""

        server = _server()

        self.assertIn("responder", server.instructions)
        self.assertIn("Diagnose and repair unhealthy Pods.", server.instructions)
        self.assertNotIn(PROCEDURE, server.instructions)


class NavigationTests(unittest.TestCase):
    def test_discover_returns_the_skeleton(self) -> None:
        server = _server()

        payload = json.loads(_text(_call(server, DISCOVER_TOOL)))
        self.assertEqual([card["ref"] for card in payload["roles"]], ["responder"])

    def test_opening_a_role_delivers_the_schemas_the_surface_no_longer_has(
        self,
    ) -> None:
        server = _server()

        opened = json.loads(_text(_call(server, OPEN_TOOL, {"ref": "responder"})))
        schemas = {tool["name"]: tool["input_schema"] for tool in opened["tools"]}
        self.assertEqual(
            sorted(schemas["get_pod_logs"]["properties"]),
            ["namespace", "pod", "previous"],
        )
        self.assertEqual(schemas["get_pod_logs"]["required"], ["namespace", "pod"])

    def test_a_wrong_ref_is_a_sentence_and_not_a_traceback(self) -> None:
        server = _server()

        with self.assertRaises(ToolError) as caught:
            _call(server, OPEN_TOOL, {"ref": "responder/banana"})

        message = str(caught.exception)
        self.assertIn("banana", message)
        self.assertIn("get_pod_logs", message)


class InvocationTests(unittest.TestCase):
    def test_a_read_only_tool_runs_through_the_read_only_door(self) -> None:
        server = _server()

        result = _call(
            server,
            INVOKE_READ_ONLY_TOOL,
            {"ref": "responder/get_pod_logs",
             "arguments": {"namespace": "prod", "pod": "api"}},
        )
        self.assertIn("prod/api", _text(result))

    def test_a_writing_tool_runs_through_the_writing_door(self) -> None:
        server = _server()

        result = _call(
            server,
            INVOKE_TOOL,
            {"ref": "responder/delete_pod",
             "arguments": {"namespace": "prod", "pod": "api"}},
        )
        self.assertIn("deleted prod/api", _text(result))

    def test_a_write_sent_through_the_read_only_door_is_refused(self) -> None:
        """The host decided whether to involve a human from the door's hint.

        Honouring a mismatch would run a write under a read-only approval.
        """

        server = _server()

        with self.assertRaises(ToolError) as caught:
            _call(
                server,
                INVOKE_READ_ONLY_TOOL,
                {"ref": "responder/delete_pod",
                 "arguments": {"namespace": "prod", "pod": "api"}},
            )

        message = str(caught.exception)
        self.assertIn("not read-only", message)
        self.assertIn(INVOKE_TOOL, message)

    def test_a_read_sent_through_the_writing_door_is_refused(self) -> None:
        server = _server()

        with self.assertRaises(ToolError) as caught:
            _call(
                server,
                INVOKE_TOOL,
                {"ref": "responder/get_pod_logs",
                 "arguments": {"namespace": "prod", "pod": "api"}},
            )

        self.assertIn(INVOKE_READ_ONLY_TOOL, str(caught.exception))

    def test_arguments_are_validated_against_the_derived_schema(self) -> None:
        """Validation left the wire with the tool; it did not stop happening."""

        server = _server()

        with self.assertRaises(ToolError) as caught:
            _call(
                server,
                INVOKE_READ_ONLY_TOOL,
                {"ref": "responder/get_pod_logs", "arguments": {"namespace": "prod"}},
            )

        self.assertIn("pod", str(caught.exception))

    def test_a_ref_that_names_a_skill_is_refused_by_invoke(self) -> None:
        server = _server()

        with self.assertRaises(ToolError) as caught:
            _call(server, INVOKE_READ_ONLY_TOOL, {"ref": "responder/diagnose"})

        self.assertIn("skill", str(caught.exception))


class ContentTests(unittest.TestCase):
    """Content is a read-only tool taking no arguments, reachable two ways.

    A model navigates to it and runs it; a host takes it up at a URI. Both
    reach one node, which is what stops the two from ever disagreeing.
    """

    def test_content_arrives_only_when_it_is_run(self) -> None:
        server = _server()

        opened = json.loads(
            _text(_call(server, OPEN_TOOL, {"ref": "responder/runbook"}))
        )
        self.assertNotIn("RUNBOOK-BODY", json.dumps(opened))
        self.assertIn(
            "RUNBOOK-BODY",
            _text(
                _call(server, INVOKE_READ_ONLY_TOOL, {"ref": "responder/runbook"})
            ),
        )

    def test_a_published_node_is_listed_on_the_resource_primitive(self) -> None:
        listed = asyncio.run(_published().list_resources())

        self.assertEqual(
            [(str(entry.uri), entry.name) for entry in listed],
            [("contexture://runbooks/crash-loop", "runbook")],
        )

    def test_reading_the_uri_returns_what_the_node_returns(self) -> None:
        contents = asyncio.run(
            _published().read_resource("contexture://runbooks/crash-loop")
        )

        self.assertIn("RUNBOOK-BODY", "".join(str(e.content) for e in contents))

    def test_a_host_read_goes_through_the_same_binding_a_model_call_does(
        self,
    ) -> None:
        """Three doors, one way of running a capability.

        Until ADR 016 this path reached the tool directly, which left the
        host's door without argument validation and without the caller in
        reach of the code that runs. The other two doors had both.
        """

        through: list[str] = []

        class Recording(TypeHintBinding):
            async def call(self, arguments, context=None):
                through.append(self.tool.name)
                return await super().call(arguments, context)

        server = serve(
            Responder,
            published=(
                Resource(
                    opens="responder/runbook",
                    uri="contexture://runbooks/crash-loop",
                    mime_type="text/markdown",
                    description="What a host may take up on its own.",
                ),
            ),
            bind=Recording,
        ).build()

        asyncio.run(server.read_resource("contexture://runbooks/crash-loop"))
        _call(server, INVOKE_READ_ONLY_TOOL, {"ref": "responder/runbook"})

        self.assertEqual(through, ["runbook", "runbook"])

    def test_publishing_something_that_takes_arguments_is_refused(self) -> None:
        """A host reads with no arguments, so what it names must answer with none."""

        with self.assertRaises(ModelValidationError) as caught:
            _published_with("responder/get_pod_logs", "contexture://logs")

        self.assertIn("takes arguments", str(caught.exception))

    def test_publishing_a_writing_tool_is_refused(self) -> None:
        with self.assertRaises(ModelValidationError) as caught:
            _published_with("responder/delete_pod", "contexture://remove")

        self.assertIn("not read-only", str(caught.exception))

    def test_publishing_a_node_that_does_not_exist_fails_on_the_way_up(self) -> None:
        with self.assertRaises(ModelValidationError) as caught:
            _published_with("responder/nope", "contexture://nope")

        self.assertIn("does not exist", str(caught.exception))


class RegistrationTests(unittest.TestCase):
    """Host config now points at the server instead of replacing it."""

    LAUNCH = Launch(
        name="contexture-demo",
        command="uv",
        args=("run", "contexture", "serve"),
    )

    def test_claude_code_config_is_a_launch_command_not_a_context_file(self) -> None:
        import json

        config = json.loads(claude_code_config(self.LAUNCH))
        entry = config["mcpServers"]["contexture-demo"]

        self.assertEqual(entry["type"], "stdio")
        self.assertEqual(entry["command"], "uv")
        self.assertEqual(entry["args"], ["run", "contexture", "serve"])

    def test_codex_config_stanza_quotes_its_command(self) -> None:
        stanza = codex_config(self.LAUNCH)

        self.assertIn("[mcp_servers.contexture-demo]", stanza)
        self.assertIn('command = "uv"', stanza)
        self.assertIn('args = ["run", "contexture", "serve"]', stanza)

    def test_both_hosts_are_given_the_same_launch_command(self) -> None:
        """The claim under test: one server, two hosts, one command."""

        commands = cli_commands(self.LAUNCH)
        suffix = "-- uv run contexture serve"

        self.assertTrue(commands["claude-code"].endswith(suffix))
        self.assertTrue(commands["codex"].endswith(suffix))
        # Claude Code defaults to local scope; a shared file needs it named.
        self.assertIn("--scope project", commands["claude-code"])


class StatelessnessTests(unittest.TestCase):
    """The gateway must answer out of the declaration and nothing else.

    Since the 2026-07-28 revision a server may not vary its surface per
    connection or as a consequence of an earlier call, and Contexture's whole
    navigation model rests on that: a `ref` is an address, not a cursor. Both
    properties hold today because nobody has written state into a gateway
    function. These tests are what makes writing some fail — a cache of "the
    role opened last", added to save tokens, would read as a reasonable
    optimization and would quietly put this server out of specification.

    Every assertion here compares a server that has done work against a server
    that has done none. Comparing two exercised servers to each other is the
    trap: a cache that degrades an answer after its first delivery poisons both
    snapshots equally, and the comparison passes while the server is already
    non-compliant. Only a cold answer is a trustworthy baseline.
    """

    @classmethod
    def setUpClass(cls) -> None:
        """Capture the cold baseline once, before any test has run.

        Every answer in it is the first call of its own server's life, and the
        sweep runs in the opposite order to `_surface`, so a payload carrying
        anything about the call before it lands where the comparison can see.
        Taking this per test instead would make each test's baseline depend on
        which tests ran first — and the earliest failure would be the only one
        that ever went red.
        """

        cls.cold = {DISCOVER_TOOL: _text(_call(_server(), DISCOVER_TOOL))}
        for ref in reversed(cls._refs()):
            cls.cold[ref] = _text(_call(_server(), OPEN_TOOL, {"ref": ref}))

    @staticmethod
    def _refs() -> list[str]:
        """Every ref the fixture addresses, read off the model, not the gateway.

        Enumerating through `discover` and `open` would spend the very calls a
        cold baseline is made of: by the time the list was in hand, every ref
        on it would already have been delivered once.
        """

        app = serve(Responder)
        refs = []
        for ref, role in app.surface.tree.index.roles_with_refs():
            refs.append(ref)
            refs.extend(
                f"{ref}/{member.name}"
                for member in (*role.skills, *role.tools)
            )
        return refs

    def _surface(self, server) -> dict[str, str]:
        """Everything `server` discloses now, as raw response text."""

        surface = {DISCOVER_TOOL: _text(_call(server, DISCOVER_TOOL))}
        for ref in self._refs():
            surface[ref] = _text(_call(server, OPEN_TOOL, {"ref": ref}))
        return surface

    def _exercise(self, server) -> None:
        """Put a server through every call it will ever be asked to serve."""

        refs = self._refs()
        for ref in refs + list(reversed(refs)):
            _call(server, OPEN_TOOL, {"ref": ref})
        _call(server, DISCOVER_TOOL)
        _call(
            server,
            INVOKE_READ_ONLY_TOOL,
            {"ref": "responder/get_pod_logs",
             "arguments": {"namespace": "prod", "pod": "api"}},
        )
        _call(
            server,
            INVOKE_READ_ONLY_TOOL,
            {"ref": "responder/runbook"},
        )

    def test_history_never_changes_an_answer(self) -> None:
        """Repetition, and the order refs are opened in, change nothing."""

        server = _server()
        self._exercise(server)

        self.assertEqual(self._surface(server), self.cold)

    def test_a_ref_resolves_on_a_server_that_was_never_discovered(self) -> None:
        """`discover` is where an agent learns a ref, not where one is issued."""

        server = _server()

        opened = json.loads(
            _text(_call(server, OPEN_TOOL, {"ref": "responder/get_pod_logs"}))
        )
        self.assertEqual(opened["name"], "get_pod_logs")

    def test_running_a_write_leaves_the_surface_untouched(self) -> None:
        """The one call most likely to be given a memory is the one that
        changes the world. It must not change this server."""

        server = _server()
        listed_before = [
            tool.model_dump(mode="json") for tool in asyncio.run(server.list_tools())
        ]

        _call(
            server,
            INVOKE_TOOL,
            {"ref": "responder/delete_pod",
             "arguments": {"namespace": "prod", "pod": "api"}},
        )

        self.assertEqual(self._surface(server), self.cold)
        self.assertEqual(
            [tool.model_dump(mode="json") for tool in asyncio.run(server.list_tools())],
            listed_before,
        )

    def test_a_failed_call_leaves_the_surface_untouched(self) -> None:
        """An error path is where a half-written cache would survive."""

        server = _server()

        for name, arguments in (
            (OPEN_TOOL, {"ref": "responder/banana"}),
            (INVOKE_READ_ONLY_TOOL, {"ref": "responder/delete_pod",
                                     "arguments": {"namespace": "p", "pod": "a"}}),
            (INVOKE_READ_ONLY_TOOL, {"ref": "responder/get_pod_logs",
                                     "arguments": {"namespace": "prod"}}),
        ):
            with self.subTest(tool=name), self.assertRaises(ToolError):
                _call(server, name, arguments)

        self.assertEqual(self._surface(server), self.cold)

    def test_a_used_server_owes_a_new_one_the_same_answers(self) -> None:
        """Two hosts connecting to one declaration are owed the same server.

        They share one sealed tree, and so share the bindings derived when it
        was sealed. The second connection must not be able to tell that the
        first one happened.
        """

        server = serve(Responder)
        first = server.build()
        self._exercise(first)
        second = server.build()

        self.assertEqual(
            tuple(tool.name for tool in asyncio.run(second.list_tools())),
            GATEWAY_TOOLS,
        )
        self.assertEqual(self._surface(second), self.cold)

    def test_a_binding_is_derived_once_when_the_tree_is_sealed(self) -> None:
        """Per tool, at seal time — not per call, and not per connection.

        This is what replaced a process-wide cache keyed by `id(tool)`. An
        address is stable and unique, so the derivation can simply be stored
        against it and never has to be invalidated or re-keyed.
        """

        derived: list[str] = []

        class Counting(TypeHintBinding):
            def __post_init__(self) -> None:
                derived.append(self.tool.name)
                super().__post_init__()

        server = serve(Responder, bind=Counting)
        once = list(derived)
        self.assertEqual(sorted(once), sorted(set(once)))

        surface = server.build()
        self._exercise(surface)
        self._exercise(server.build())

        self.assertEqual(derived, once)
        self.assertEqual(self._surface(surface), self.cold)


#: Claude Code truncates server instructions at this many bytes.
HOST_LIMIT = 2048

#: Shapes to hold the roster to, as (branching factor, depth). The first two
#: fit whole; the rest are past the budget, which is where the rules bite.
ROSTER_SHAPES = ((2, 2), (5, 3), (8, 3), (3, 4), (10, 4))


def _forest(branch: int, depth: int) -> list[Role]:
    """A uniform forest, for asking what the roster does when it runs out."""

    def build(ref: str, remaining: int) -> Role:
        children = (
            [build(f"{ref}-{i}", remaining - 1) for i in range(branch)]
            if remaining > 1
            else []
        )
        return Role(
            name=ref,
            description=f"Role {ref} does something specific.",
            instructions="Do the thing.",
            children=children,
        )

    return [build(f"r{i}", depth) for i in range(branch)]


def _listed(text: str) -> list[str]:
    """The refs a roster actually names, ignoring its truncation line."""

    return re.findall(r"^- ([^:.][^:]*):", text, re.MULTILINE)


class ToolDisclosureTests(unittest.TestCase):
    """What a tool tells an agent about how to call it.

    A tool is reachable two ways — as a card in its role, and by opening its
    ref — and both have to describe the same call. They did not: the card
    carried the derived input schema, while opening the tool listed the raw
    parameter names off `invoke`. That list included any parameter the
    framework fills rather than the model, so the payload named an argument the
    schema would reject.
    """

    def test_both_ways_to_a_tool_describe_the_same_call(self) -> None:
        app = serve(Responder)

        card = next(
            tool
            for tool in app.surface.tree.open("responder")["tools"]
            if tool["name"] == "get_pod_logs"
        )
        opened = app.surface.tree.open("responder/get_pod_logs")

        self.assertEqual(opened["input_schema"], card["input_schema"])
        self.assertEqual(opened["read_only"], card["read_only"])

    def test_a_disclosed_schema_carries_no_derived_titles(self) -> None:
        """`"title": "invokeArguments"` is a pydantic artefact, not information.

        It is the name of the model the SDK built from `invoke`, and the
        per-property titles under it are the parameter names capitalised. Both
        reach the agent on every tool card of every open.
        """

        app = serve(Responder)
        schema = app.surface.tree.open("responder/get_pod_logs")["input_schema"]

        self.assertNotIn("title", schema)
        self.assertEqual(
            [key for value in schema["properties"].values() for key in value],
            ["type", "type", "default", "type"],
        )

    def test_a_parameter_named_title_survives_the_stripping(self) -> None:
        """The obvious way to strip titles deletes this tool's first argument."""

        class Publish(Tool):
            """Publish a note."""

            def __init__(self) -> None:
                super().__init__(
                    name="publish",
                    description="Publish a note.",
                    read_only=False,
                )

            async def invoke(self, title: str, body: str = "") -> str:
                return title

        class Notes(Role):
            def __init__(self) -> None:
                super().__init__(
                    name="notes",
                    description="Keep notes.",
                    instructions="Write it down.",
                    tools=[Publish()],
                )

        app = serve(Notes)
        schema = app.surface.tree.open("notes/publish")["input_schema"]

        self.assertEqual(sorted(schema["properties"]), ["body", "title"])
        self.assertNotIn("title", schema["properties"]["title"])

    def test_stripping_the_disclosed_schema_leaves_validation_alone(self) -> None:
        """The SDK validates against its own copy, which keeps its titles."""

        server = _server()

        self.assertIn(
            "previous=True",
            _text(
                _call(
                    server,
                    INVOKE_READ_ONLY_TOOL,
                    {
                        "ref": "responder/get_pod_logs",
                        "arguments": {
                            "namespace": "prod",
                            "pod": "api",
                            "previous": True,
                        },
                    },
                )
            ),
        )

    def test_a_framework_filled_parameter_is_never_disclosed(self) -> None:
        """`ctx` is the framework's to fill, so an agent must not be told of it.

        The SDK keeps it out of the schema on its own. What it cannot do is
        keep it out of a list `core` builds by reading the signature, because
        `core` has no way to tell the two kinds of parameter apart — which is
        why a disclosure payload now carries the schema and nothing else.
        """

        class Progressing(Tool):
            """Do something slow enough to report on."""

            def __init__(self) -> None:
                super().__init__(
                    name="progressing",
                    description="Do something slow enough to report on.",
                    read_only=True,
                )

            async def invoke(self, ctx: Context, target: str) -> str:
                return target

        class Slow(Role):
            def __init__(self) -> None:
                super().__init__(
                    name="slow",
                    description="Run something slow.",
                    instructions="Report progress.",
                    tools=[Progressing()],
                )

        app = serve(Slow)
        opened = app.surface.tree.open("slow/progressing")
        card = app.surface.tree.open("slow")["tools"][0]

        for payload in (opened, card):
            with self.subTest(payload=payload.get("ref")):
                self.assertNotIn("ctx", json.dumps(payload))
                self.assertEqual(
                    sorted(payload["input_schema"]["properties"]), ["target"]
                )

        # The signature itself still reports every parameter: it describes the
        # function, not the call, and the framework fills one of them.
        self.assertEqual(Progressing().parameters(), ("ctx", "target"))


class RosterTests(unittest.TestCase):
    """What the bootstrap roster promises when it cannot say everything.

    The roster is the only thing a host reads before calling anything, and it
    is budgeted, so it is always at risk of describing a choice that is not the
    real one. ADR 004 stated the rule and ADR 007 kept it: every sibling is
    visible before the choice, and what cannot be seen together is opened
    rather than guessed between.
    """

    def _partial_groups(self, tree: Disclosure, text: str) -> list[str]:
        """Parents the roster names some — but not all — of the children of."""

        held: dict[str, int] = {}
        for ref, _ in tree.index.roles_with_refs():
            if SEPARATOR in ref:
                parent = ref.rsplit(SEPARATOR, 1)[0]
                held[parent] = held.get(parent, 0) + 1

        shown: dict[str, int] = {}
        for ref in _listed(text):
            if SEPARATOR in ref:
                parent = ref.rsplit(SEPARATOR, 1)[0]
                shown[parent] = shown.get(parent, 0) + 1

        return [
            parent
            for parent, total in held.items()
            if 0 < shown.get(parent, 0) < total
        ]

    def test_a_sibling_set_is_never_shown_in_part(self) -> None:
        """Three of a role's eight sub-roles look like the whole choice.

        This is the failure the budget produces on its own: it stops mid-group,
        and nothing in the text says the group it stopped inside of was cut.
        Listing none of that role's children is strictly better — the reader
        then knows to open it.
        """

        for branch, depth in ROSTER_SHAPES:
            with self.subTest(shape=f"{branch}x{depth}"):
                tree = Disclosure.of(_forest(branch, depth))
                text = instructions.build(tree)

                self.assertEqual(self._partial_groups(tree, text), [])

    def test_the_roster_fits_the_budget_the_host_actually_enforces(self) -> None:
        """Whole groups must not be bought by overrunning the host's limit."""

        for branch, depth in ROSTER_SHAPES:
            with self.subTest(shape=f"{branch}x{depth}"):
                tree = Disclosure.of(_forest(branch, depth))

                self.assertLessEqual(len(instructions.build(tree)), HOST_LIMIT)

    def test_a_forest_that_fits_is_listed_whole(self) -> None:
        """Group-wise spending must not cost a small server its full roster."""

        tree = Disclosure.of(_forest(2, 2))
        listed = _listed(instructions.build(tree))

        self.assertEqual(len(listed), len(list(tree.index.roles_with_refs())))
        self.assertNotIn("...and", instructions.build(tree))

    def test_what_was_dropped_is_counted_and_pointed_at(self) -> None:
        tree = Disclosure.of(_forest(8, 3))
        text = instructions.build(tree)

        total = len(list(tree.index.roles_with_refs()))
        line = next(l for l in text.splitlines() if l.startswith("- ...and"))

        self.assertIn(f"{total - len(_listed(text))} more role(s)", line)
        self.assertIn("open one of the roles above", line)

    def test_roots_are_cut_last_and_recovered_by_a_named_call(self) -> None:
        """A root is the first segment of every ref beneath it.

        Cutting the root list silently would hide whole branches with no way
        back. It is also the one cut a single call undoes, because since
        ADR 007 the roots are exactly what discover answers with — so this is
        the one place the roster may show a group in part, and it has to say
        which call completes it.
        """

        crowded = Disclosure.of(
            [
                Role(name=f"root-{i:03d}", description="D" * 70, instructions="x")
                for i in range(60)
            ]
        )
        text = instructions.build(crowded)
        line = next(l for l in text.splitlines() if l.startswith("- ...and"))

        self.assertLessEqual(len(text), HOST_LIMIT)
        self.assertIn("more root role(s)", line)
        self.assertIn(DISCOVER_TOOL, line)
        self.assertGreater(len(_listed(text)), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class _GenerateCover(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="generate_cover",
            description="Generate a cover image for a letter.",
            read_only=False,
        )

    async def invoke(self, topic: str) -> str:
        return f"cover-{topic}.png"


class CommandPlaneTests(unittest.IsolatedAsyncioTestCase):
    """The person's half of the surface.

    MCP splits its primitives by who decides, and until this existed Contexture
    occupied only the model's side of that split. Nothing about progressive
    disclosure changes here — what grows is the set of who may trigger it.

    The tree says nothing about any of this. Who may trigger a disclosure is a
    fact about the protocol surface, so it is declared beside it and never on
    the node: the object model does not know that people exist.
    """

    #: The one node this fixture puts on the prompt primitive.
    SHIP = "oc/publishing/compose-and-ship"

    @classmethod
    def _published(cls, *, model_may_open: bool = False) -> tuple[Prompt, ...]:
        return (
            Prompt(
                opens=cls.SHIP,
                description="Assemble the weekly letter and send it.",
                model_may_open=model_may_open,
            ),
        )

    @staticmethod
    def _tree() -> Disclosure:
        ship = Skill(
            name="compose-and-ship",
            description="Assemble the weekly letter and send it.",
            instructions="1. Generate the cover.\n2. Ship it.",
            uses=("oc/assets/generate_cover",),
        )
        assets = Role(
            name="assets",
            description="Owns produced media.",
            instructions="Generate media on request.",
            tools=[_GenerateCover()],
        )
        publishing = Role(
            name="publishing",
            description="Owns what goes out.",
            instructions="Compose, then ship.",
            skills=[ship],
        )
        return Disclosure.of(
            Role(
                name="oc",
                description="One-creator.",
                instructions="Route to the branch that owns the outcome.",
                children=[assets, publishing],
            ),
            bind=TypeHintBinding,
        )

    def _server(
        self,
        tree: Disclosure,
        publish: tuple[Prompt, ...] | None = None,
    ) -> MCPServer:
        prompts = self._published() if publish is None else publish
        surface = Surface.of(tree, prompts=prompts)
        wire = MCPServer(name="oc", version="0", instructions="x")
        surface.install(wire)
        return wire

    async def test_only_a_marked_node_reaches_the_prompt_plane(self) -> None:
        """The command surface is authored, not derived from the forest.

        Four nodes here and one command, because one node was marked. The
        generic entrance stands beside it and is not derived from the forest
        either — it is one prompt whatever the tree holds.
        """

        prompts = await self._server(self._tree()).list_prompts()

        self.assertEqual(
            sorted(prompt.name for prompt in prompts),
            ["compose-and-ship", "goto"],
        )

    async def test_an_unmarked_forest_offers_only_the_generic_entrance(self) -> None:
        """The default is the model's plane alone, and it stays that way.

        A declaration that says nothing about people gets no menu to maintain
        — which is the whole reason the default is inverted from the
        convention elsewhere.
        """

        prompts = await self._server(self._tree(), publish=()).list_prompts()

        self.assertEqual([prompt.name for prompt in prompts], ["goto"])

    async def test_a_command_answers_with_what_open_would_have_said(self) -> None:
        """Two doors, one answer.

        `open` already refuses to describe a capability two ways. A command is
        a second door onto the same node, so it differs in who may knock and
        in nothing else.
        """

        tree = self._tree()
        result = await self._server(tree).get_prompt("compose-and-ship", {})
        (message,) = result.messages
        text = message.content.text

        payload = tree.open("oc/publishing/compose-and-ship")
        self.assertIn(json.dumps(payload, ensure_ascii=False, indent=2), text)

    async def test_a_command_arrives_as_something_the_person_said(self) -> None:
        """The protocol's word for this plane is user-controlled.

        Dressing the server's answer as an assistant turn would claim the model
        said something it did not.
        """

        result = await self._server(self._tree()).get_prompt("compose-and-ship", {})

        self.assertEqual([message.role for message in result.messages], ["user"])

    async def test_a_direct_hit_carries_signposts_and_calls_them_undisclosed(
        self,
    ) -> None:
        """ADR 004's rule, kept at an entrance that has no way down.

        Arriving directly skips the calls that would have shown what sat beside
        the node on the way. The signpost reports that siblings exist and how
        many, and never their names.
        """

        result = await self._server(self._tree()).get_prompt("compose-and-ship", {})
        text = result.messages[0].content.text

        self.assertIn("oc: 2 sub-role(s) here", text)
        self.assertIn("not disclosed", text)
        # The sibling branch is counted, never named.
        self.assertNotIn("oc/assets:", text)

    async def test_a_referenced_capability_arrives_callable(self) -> None:
        """The command is one round trip, not one plus a schema fetch."""

        result = await self._server(self._tree()).get_prompt("compose-and-ship", {})

        self.assertIn("oc/assets/generate_cover", result.messages[0].content.text)
        self.assertIn("input_schema", result.messages[0].content.text)

    async def test_the_model_is_refused_the_door_reserved_for_a_person(self) -> None:
        """Refused where the door is known, exactly as a wrong-door invoke is.

        The tree serves both doors; only this layer knows which one a call
        arrived through.
        """

        server = self._server(self._tree())

        with self.assertRaises(ToolError) as caught:
            await server.call_tool(
                OPEN_TOOL, {"ref": "oc/publishing/compose-and-ship"}
            )

        message = str(caught.exception)
        self.assertIn("opened by a person", message)
        self.assertIn("tell the user which command", message)

    async def test_the_refused_node_keeps_its_card(self) -> None:
        """A guardrail that lets the model point beats one that only hides.

        The model may not enter, but it can see that the capability exists and
        say which prompt reaches it — which it cannot do if the card is gone.
        The card is the tree's and says nothing about who may open it: the
        object model does not know that people exist.
        """

        opened = self._tree().open("oc/publishing")

        (card,) = opened["skills"]
        self.assertEqual(card["name"], "compose-and-ship")
        self.assertEqual(card["ref"], self.SHIP)

    async def test_a_node_on_both_planes_is_open_to_both(self) -> None:
        tree = self._tree()
        server = self._server(tree, self._published(model_may_open=True))

        prompts = await server.list_prompts()
        opened = await server.call_tool(
            OPEN_TOOL, {"ref": "oc/publishing/compose-and-ship"}
        )

        self.assertIn("compose-and-ship", [prompt.name for prompt in prompts])
        self.assertIsNotNone(opened)
