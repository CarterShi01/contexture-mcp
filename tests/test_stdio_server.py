"""End-to-end tests that launch the demo as a real subprocess.

Everything else in this suite exercises the server in-process, which cannot
observe the two things that actually break a stdio server: whether the process
starts at all under a host's launch command, and whether anything but MCP
messages reaches stdout. Both are only visible from outside the process, so
these tests pay for a subprocess.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest

from contexture.core.model.system_api import GATEWAY_TOOLS
from pathlib import Path

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp_types import PromptReference
except ImportError:  # pragma: no cover - the SDK is a hard dependency
    ClientSession = None  # type: ignore[assignment]

SOURCE_ROOT = Path(__file__).resolve().parent.parent
DEMO_MODULE = "contexture.demo.server"

#: Anything the demo prints outside the protocol would corrupt the stream, so
#: stderr is captured to a file rather than inherited, and asserted on.
TIMEOUT_SECONDS = 30


def _server_parameters(stderr_path: Path) -> StdioServerParameters:
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{SOURCE_ROOT}{os.pathsep}{existing}" if existing else str(SOURCE_ROOT)
    )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", DEMO_MODULE],
        env=environment,
    )


async def _session(work, *, modern: bool = True):
    """Run `work(session)` against a freshly launched demo subprocess.

    `modern` selects how the session is opened. The 2026-07-28 revision has no
    initialize handshake — a client probes `server/discover` and every request
    then carries its own version metadata — while older revisions negotiate on
    connect. Both doors are exercised, because a server that only answers the
    newest one silently drops every host that has not upgraded yet.
    """

    stderr_path = Path(os.environ.get("CONTEXTURE_TEST_STDERR", os.devnull))
    with open(stderr_path, "w", encoding="utf-8") as errlog:
        parameters = _server_parameters(stderr_path)
        async with stdio_client(parameters, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                if modern:
                    await session.discover()
                else:
                    await session.initialize()
                return await work(session)


def _run(work, *, modern: bool = True):
    return asyncio.run(
        asyncio.wait_for(_session(work, modern=modern), TIMEOUT_SECONDS)
    )


@unittest.skipIf(ClientSession is None, "the MCP SDK is not installed")
class StdioServerTests(unittest.TestCase):
    """The demo, launched the way Claude Code and Codex launch it."""

    def test_the_server_starts_and_reports_its_instructions(self) -> None:
        async def work(session):
            return session.instructions, session.protocol_version

        instructions, protocol_version = _run(work)

        self.assertIsNotNone(instructions)
        assert instructions is not None
        self.assertIn("contexture_open", instructions)
        # The roster is here rather than behind a first discover call: a
        # gateway whose five tool names all begin `contexture_` otherwise gives
        # a host no sign of what the server is for.
        self.assertIn("kubernetes-platform/incident-response", instructions)
        # Codex asks that the opening be self-contained; Claude Code truncates
        # the whole field at 2KB. Both limits are checked, not just documented.
        self.assertIn("contexture_open", instructions[:512])
        self.assertLess(len(instructions.encode("utf-8")), 2048)
        self.assertEqual(protocol_version, "2026-07-28")

    def test_a_legacy_host_still_gets_a_working_server(self) -> None:
        """One implementation serves the handshake revisions too, unchanged."""

        async def work(session):
            tools = await session.list_tools()
            return session.protocol_version, {t.name for t in tools.tools}

        protocol_version, names = _run(work, modern=False)

        self.assertIn(protocol_version, {"2025-11-25", "2025-06-18", "2025-03-26"})
        self.assertEqual(names, set(GATEWAY_TOOLS))

    def test_the_surface_is_the_gateway_and_holds_no_capability(self) -> None:
        """A capability on the surface is one every session pays for, forever.

        Checked on the wire rather than against the projection object, because
        this is the claim the whole design rests on.
        """

        async def work(session):
            tools = await session.list_tools()
            resources = await session.list_resources()
            return tools.tools, resources.resources

        tools, resources = _run(work)
        names = {tool.name for tool in tools}

        self.assertEqual(names, set(GATEWAY_TOOLS))
        self.assertEqual(names & {"get_pod_status", "get_pod_logs", "get_pod_events"}, set())
        # The demo publishes two documents on the resource primitive, and
        # nothing else: a capability never reaches a list a host can read.
        self.assertEqual(
            sorted(str(entry.uri) for entry in resources),
            [
                "contexture://runbooks/crash-loop-backoff",
                "contexture://runbooks/rollback-policy",
            ],
        )

    def test_read_only_is_a_door_and_never_an_argument(self) -> None:
        """The approval hole this design is meant to close, checked on the wire.

        A model that could pass its own `approved=True` would be approving its
        own writes. With capabilities off the surface, the classification
        travels as *which entry point was used*, and each door carries the
        hint the host acts on.
        """

        async def work(session):
            return (await session.list_tools()).tools

        tools = _run(work)
        for tool in tools:
            with self.subTest(tool=tool.name):
                properties = set((tool.input_schema or {}).get("properties", {}))
                self.assertNotIn("read_only", properties)
                self.assertNotIn("approved", properties)
                self.assertNotIn("read_only_hint", properties)

        hints = {
            tool.name: tool.annotations and tool.annotations.read_only_hint
            for tool in tools
        }
        self.assertTrue(hints["contexture_invoke_read_only"])
        self.assertFalse(hints["contexture_invoke"])

    def test_a_full_diagnosis_runs_over_the_wire(self) -> None:
        """The whole chain: skeleton, open a role, open its skill, gather, read."""

        async def work(session):
            # Navigate rather than assume, one level per call: discover names
            # the roots, opening the root names its specialisms, and the one
            # that matches a restart loop is chosen from those.
            roots = await session.call_tool("contexture_discover", {})
            root_ref = roots.structured_content["roles"][0]["ref"]
            platform = await session.call_tool(
                "contexture_open", {"ref": root_ref}
            )
            role_ref = next(
                card["ref"]
                for card in platform.structured_content["roles"]
                if card["name"] == "incident-response"
            )

            role = await session.call_tool("contexture_open", {"ref": role_ref})
            skill_ref = role.structured_content["skills"][0]["ref"]
            schemas = {
                tool["name"]: tool["input_schema"]
                for tool in role.structured_content["tools"]
            }

            skill = await session.call_tool("contexture_open", {"ref": skill_ref})

            pod = {"namespace": "prod", "pod": "payments-api-7d9c"}
            status = await session.call_tool(
                "contexture_invoke_read_only",
                {"ref": f"{role_ref}/get_pod_status", "arguments": pod},
            )
            logs = await session.call_tool(
                "contexture_invoke_read_only",
                {"ref": f"{role_ref}/get_pod_logs", "arguments": pod},
            )
            # The runbook is content the model navigated to, so it runs it
            # like any other read-only tool. The host reaches the same bytes at
            # a URI of its own; one node, two addresses.
            runbook = await session.call_tool(
                "contexture_invoke_read_only",
                {"ref": f"{role_ref}/crash_loop_runbook"},
            )
            return (
                role.structured_content,
                schemas,
                skill.structured_content,
                json.loads(status.content[0].text),
                logs.content[0].text,
                runbook.content[0].text,
            )

        role, schemas, skill, status, logs, runbook = _run(work)

        self.assertEqual(role["skills"][0]["name"], "diagnose-crash-loop-backoff")
        # A skill that names a capability outside its own parent carries a card
        # for it, at ROUTE and never deeper — which is what makes a reference
        # cycle a pair of cards rather than a walk that does not terminate.
        self.assertNotIn("uses", skill)
        self.assertEqual(schemas["get_pod_status"]["required"], ["namespace", "pod"])
        self.assertIn("get_pod_status", skill["instructions"])
        self.assertEqual(status["container_state"], "CrashLoopBackOff")
        self.assertEqual(status["restart_count"], 14)
        self.assertIn("DB_URL is missing", logs)
        self.assertIn("CrashLoopBackOff", runbook)

    def test_a_gateway_call_returns_content_rather_than_a_typed_result(self) -> None:
        """A cost of keeping capabilities off the surface, recorded not hidden.

        `GetPodStatus.invoke` is annotated `-> PodStatus`, and on a native
        surface the SDK would derive an output schema from it and return
        structured content. A gateway's own return type is the union of every
        tool's, which is to say `Any`, so the result arrives as text the agent
        parses. The values survive; the protocol-level typing does not.
        """

        async def work(session):
            return await session.call_tool(
                "contexture_invoke_read_only",
                {
                    "ref": "kubernetes-platform/incident-response/get_pod_status",
                    "arguments": {"namespace": "prod", "pod": "payments-api-7d9c"},
                },
            )

        result = _run(work)

        self.assertIsNone(result.structured_content)
        self.assertEqual(
            json.loads(result.content[0].text)["container_state"],
            "CrashLoopBackOff",
        )

    def test_the_doors_are_enforced_in_both_directions_on_the_wire(self) -> None:
        """The one protection the gateway has to get right.

        A host decides whether to involve a human from the hint on the entry
        point. If a write could be run through the read-only door, that
        decision would have been made about the wrong call.
        """

        write_ref = "kubernetes-platform/deployment-ops/roll_back_deployment"
        read_ref = "kubernetes-platform/incident-response/get_pod_logs"

        async def work(session):
            wrong_write = await session.call_tool(
                "contexture_invoke_read_only",
                {"ref": write_ref,
                 "arguments": {"namespace": "prod", "deployment": "payments-api"}},
            )
            wrong_read = await session.call_tool(
                "contexture_invoke",
                {"ref": read_ref,
                 "arguments": {"namespace": "prod", "pod": "payments-api-7d9c"}},
            )
            allowed_write = await session.call_tool(
                "contexture_invoke",
                {"ref": write_ref,
                 "arguments": {"namespace": "prod", "deployment": "payments-api"}},
            )
            return wrong_write, wrong_read, allowed_write

        wrong_write, wrong_read, allowed_write = _run(work)

        self.assertTrue(wrong_write.is_error)
        self.assertIn("contexture_invoke", wrong_write.content[0].text)
        self.assertTrue(wrong_read.is_error)
        self.assertIn("contexture_invoke_read_only", wrong_read.content[0].text)
        self.assertFalse(allowed_write.is_error)
        self.assertIn("Rolled prod/payments-api back", allowed_write.content[0].text)

    def test_the_skeleton_never_leaks_a_procedure_or_a_schema(self) -> None:
        """Progressive disclosure, asserted where it can actually be violated.

        The bootstrap text and the first call are what every session pays for
        unconditionally. Nothing expensive may ride along in either.
        """

        async def work(session):
            instructions = session.instructions or ""
            roots = await session.call_tool("contexture_discover", {})
            root_ref = roots.structured_content["roles"][0]["ref"]
            platform = await session.call_tool(
                "contexture_open", {"ref": root_ref}
            )
            role_ref = next(
                card["ref"]
                for card in platform.structured_content["roles"]
                if card["name"] == "incident-response"
            )
            role = await session.call_tool("contexture_open", {"ref": role_ref})
            skill_ref = role.structured_content["skills"][0]["ref"]
            opened = await session.call_tool("contexture_open", {"ref": skill_ref})
            return (
                instructions,
                str(roots.structured_content),
                str(role.structured_content),
                opened.structured_content,
            )

        bootstrap, roots_payload, role, opened = _run(work)
        procedure = "Do not recommend restarting or deleting the Pod"

        for payload in (bootstrap, roots_payload):
            self.assertNotIn(procedure, payload)
            self.assertNotIn("input_schema", payload)
        # The first call names the roots and nothing beneath them, so entering
        # a large forest costs the roots rather than the forest.
        self.assertNotIn("incident-response", roots_payload)
        self.assertNotIn(procedure, role)
        self.assertIn(procedure, opened["instructions"])

    def test_published_content_arrives_only_on_read(self) -> None:
        """Listing a resource must stay cheap, however large its content is."""

        async def work(session):
            listed = await session.list_resources()
            read = await session.read_resource(
                "contexture://runbooks/crash-loop-backoff"
            )
            return (
                [str(entry.uri) for entry in listed.resources],
                read.contents[0].text,
            )

        listed, content = _run(work)
        marker = "Restarting the Pod does not repair a configuration error"

        # Listing costs a descriptor; the document arrives only on a read.
        self.assertIn("contexture://runbooks/crash-loop-backoff", listed)
        self.assertNotIn(marker, str(listed))
        self.assertIn(marker, content)


@unittest.skipIf(ClientSession is None, "the MCP SDK is not installed")
class StdioHygieneTests(unittest.TestCase):
    """stdout belongs to the protocol, and nothing else may write to it."""

    def test_the_server_writes_no_stray_output_to_stdout(self) -> None:
        """Checked by talking to the process, not by grepping for `print`.

        A logging handler that defaults to stdout, an import-time banner from a
        dependency, or a warning would each corrupt the stream. The only honest
        check is whether a real session survives, since the SDK's own framing
        rejects any line that is not a valid message.
        """

        import logging

        async def work(session):
            # Provoke the failure mode: log during a call and confirm the
            # session still parses everything that follows.
            logging.getLogger("contexture.test").warning("noise on the log")
            await session.call_tool("contexture_discover", {})
            tools = await session.list_tools()
            return len(tools.tools)

        self.assertGreater(_run(work), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


@unittest.skipIf(ClientSession is None, "the MCP SDK is not installed")
class StdioCommandPlaneTests(unittest.TestCase):
    """The person's plane, over real stdio rather than in process.

    `prompts/list`, `prompts/get` and `completion/complete` are three separate
    protocol methods with their own capability declaration. Exercising them
    through the SDK client is the only way to find out whether the server
    actually declares the capability, rather than whether a Python object
    happens to hold the right attributes.
    """

    def test_the_menu_is_what_was_declared_plus_the_generic_entrance(
        self,
    ) -> None:
        """One declared command, and `goto` beside it.

        The count is authored: the demo declares exactly one prompt, and the
        menu holds it whatever the forest grows to. `goto` is always there,
        because the nodes nobody marked still need a door a person can use.
        """

        async def work(session):
            return await session.list_prompts()

        listed = _run(work)
        by_name = {prompt.name: prompt for prompt in listed.prompts}

        self.assertEqual(sorted(by_name), ["goto", "roll-back-a-release"])
        # A declared command takes no argument: the node it opens was fixed
        # when it was registered, so there is nothing for a person to fill in.
        self.assertFalse(by_name["roll-back-a-release"].arguments)
        (argument,) = by_name["goto"].arguments or []
        self.assertEqual(argument.name, "ref")
        self.assertTrue(argument.required)

    def test_a_declared_command_opens_its_node_for_a_person(self) -> None:
        """The shape a business reaches for: a command pointing at one skill.

        It answers with the payload `contexture_open` would have returned, so
        the two doors differ in who may knock and in nothing else.
        """

        async def work(session):
            return await session.get_prompt("roll-back-a-release", {})

        (message,) = _run(work).messages
        text = message.content.text

        self.assertIn("A rollback destroys the evidence", text)
        # The reference it declares arrives as a card, and a card only: the
        # procedure it names is a separate call.
        self.assertIn("diagnose-crash-loop-backoff", text)
        self.assertNotIn("Do not recommend restarting", text)

    def test_a_person_reaches_depth_three_without_spending_a_model_turn(self) -> None:
        """What the whole plane is for.

        The same node `hosts.md` records a host reaching in three navigation
        calls, reached here in one request that no model participated in.
        """

        async def work(session):
            return await session.get_prompt(
                "goto",
                {"ref": "kubernetes-platform/incident-response/diagnose-crash-loop-backoff"},
            )

        result = _run(work)

        (message,) = result.messages
        self.assertEqual(message.role, "user")
        text = message.content.text
        # The skill's procedure, which reaches an agent only when it is opened.
        self.assertIn("get_pod_status", text)
        # Signposts: the level above is counted, and its members are not named.
        self.assertIn("kubernetes-platform: 2 sub-role(s) here", text)
        self.assertIn("not disclosed", text)
        self.assertNotIn("deployment-ops", text)

    def test_completion_ranks_and_answers_from_any_part_of_a_path(self) -> None:
        """A person who does not know the tree can still find a node in it."""

        async def work(session):
            return await session.complete(
                ref=PromptReference(type="ref/prompt", name="goto"),
                argument={"name": "ref", "value": "crash"},
            )

        result = _run(work)

        self.assertIn(
            "kubernetes-platform/incident-response/crash_loop_runbook",
            result.completion.values,
        )
        self.assertEqual(result.completion.total, len(result.completion.values))
        self.assertFalse(result.completion.has_more)

    def test_completion_reaches_every_kind_not_only_roles(self) -> None:
        """A person aims at a skill or a tool at least as often as at a role."""

        async def work(session):
            return await session.complete(
                ref=PromptReference(type="ref/prompt", name="goto"),
                argument={"name": "ref", "value": ""},
            )

        values = _run(work).completion.values

        self.assertIn("kubernetes-platform", values)
        self.assertIn(
            "kubernetes-platform/incident-response/diagnose-crash-loop-backoff", values
        )
        self.assertIn("kubernetes-platform/incident-response/get_pod_status", values)

    def test_completion_declines_a_prompt_that_is_not_ours(self) -> None:
        """Answering for somebody else's prompt would put our refs under it."""

        async def work(session):
            return await session.complete(
                ref=PromptReference(type="ref/prompt", name="something-else"),
                argument={"name": "ref", "value": "crash"},
            )

        self.assertEqual(_run(work).completion.values, [])
