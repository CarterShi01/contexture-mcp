"""Tests for the disclosure inspector.

This command has exactly one claim: what it prints is what the server sends.
A debugging tool that renders its own idea of the payload is worse than none,
because it is trusted. `HonestyTests` is therefore the centre of this file —
every payload is compared against what the real gateway answers with, called
through the SDK the same way `test_binding` calls it.

The rest covers what the command is for: the three host limits that fail
silently, the refusal an agent reads when a ref is wrong, and the sweep that
walks every node so a developer can read all of their own text at once.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from contexture import inspection
from contexture.core.constants import DISCOVER_TOOL, OPEN_TOOL
from contexture.core.model.role import Role
from contexture.core.model.skill import Skill
from contexture.core.model.tool import Tool
import sys
from pathlib import Path

from contexture.server import ContextureServer, instructions

# Discovery puts `tests/` on the path, but running one module by name does not.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from serving import serve  # noqa: E402
from contexture.core.model.disclosure import Disclosure


class GetPodLogs(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="get_pod_logs",
            description="Return the recent container logs for a Pod.",
            read_only=True,
        )

    async def invoke(self, namespace: str, pod: str, previous: bool = False) -> str:
        return f"{namespace}/{pod} previous={previous}"


class Runbook(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="runbook",
            description="How to diagnose a container that keeps restarting.",
            read_only=True,
        )

    async def invoke(self) -> str:
        return "RUNBOOK-BODY"


class Diagnose(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="diagnose",
            description="Find why a Pod restarts repeatedly.",
            instructions="Read the status, then the logs.",
        )


class Responder(Role):
    def __init__(self) -> None:
        super().__init__(
            name="responder",
            description="Diagnose and repair unhealthy Pods.",
            instructions="Inspect before changing anything.",
            skills=[Diagnose()],
            tools=[GetPodLogs(), Runbook()],
        )


class Platform(Role):
    def __init__(self) -> None:
        super().__init__(
            name="platform",
            description="Operate the platform.",
            instructions="Route to the branch that owns the question.",
            children=[Responder()],
        )


def _app() -> ContextureServer:
    return serve(Platform)


def _served(app: ContextureServer, name: str, arguments: dict | None = None):
    """What the real gateway answers, parsed back out of the wire text."""

    server = app.build()
    result = asyncio.run(server.call_tool(name, arguments or {}))
    return json.loads(result.content[0].text)


def _wide_tree(roles: int) -> Disclosure:
    return Disclosure.of(
        [
            Role(
                name=f"role-{index:02d}",
                description="A role that exists to take up room in the roster.",
                instructions="Do the work.",
            )
            for index in range(roles)
        ]
    )


class HonestyTests(unittest.TestCase):
    """The payload printed is the payload served, or this tool is worthless."""

    def test_the_discover_step_is_what_discover_answers(self) -> None:
        app = _app()
        step = inspection.discover_step(app.surface.tree)

        self.assertEqual(step.payload, _served(app, DISCOVER_TOOL))
        self.assertEqual(json.loads(step.body), _served(app, DISCOVER_TOOL))

    def test_every_open_step_is_what_open_answers(self) -> None:
        app = _app()
        for ref in inspection.every_ref(app.surface.tree):
            with self.subTest(ref=ref):
                step = inspection.open_step(app.surface.tree, ref)
                self.assertFalse(step.refused)
                self.assertEqual(
                    step.payload, _served(app, OPEN_TOOL, {"ref": ref})
                )

    def test_a_tool_card_is_inspected_with_the_schema_it_is_served_with(
        self,
    ) -> None:
        """The schema comes from the tool's real binding, not an empty stand-in.

        A tree built without one reports `{}` for every tool, which
        would make this command quietly useless for the one payload a model has
        to get exactly right.
        """

        app = _app()
        step = inspection.open_step(app.surface.tree, "platform/responder/get_pod_logs")

        assert step.payload is not None
        self.assertIn("previous", step.payload["input_schema"]["properties"])

    def test_the_connect_step_carries_the_instructions_the_server_reports(
        self,
    ) -> None:
        app = _app()
        text = instructions.build(app.surface.tree)

        self.assertEqual(app.build().instructions, text)
        self.assertEqual(inspection.connect_step(app.surface.tree, text).body, text)


class ConnectStepTests(unittest.TestCase):
    """The three ways the bootstrap text fails, all of them silently."""

    def test_text_over_the_host_limit_is_reported_as_over(self) -> None:
        tree = _wide_tree(80)
        text = instructions.build(tree, budget=100_000)
        step = inspection.connect_step(tree, text)

        self.assertGreater(step.cost.bytes, instructions.INSTRUCTIONS_LIMIT)
        self.assertFalse(step.checks[0].ok)
        self.assertIn(str(instructions.INSTRUCTIONS_LIMIT), step.checks[0].note)

    def test_text_within_the_host_limit_passes_every_check(self) -> None:
        app = _app()
        step = inspection.connect_step(app.surface.tree, instructions.build(app.surface.tree))

        self.assertTrue(all(check.ok for check in step.checks))

    def test_an_opening_that_does_not_stand_alone_is_reported(self) -> None:
        app = _app()
        step = inspection.connect_step(app.surface.tree, "Roles:\n- platform: a thing.")

        self.assertFalse(step.checks[1].ok)
        self.assertIn(str(instructions.SELF_CONTAINED_PREFIX), step.checks[1].note)

    def test_a_cut_roster_is_reported_as_cut(self) -> None:
        """The failure the verification run had to be set up by hand to see."""

        tree = _wide_tree(40)
        step = inspection.connect_step(tree, instructions.build(tree, budget=200))

        self.assertFalse(step.checks[2].ok)
        self.assertIn("cut", step.checks[2].note)
        self.assertIn(DISCOVER_TOOL, step.body)

    def test_the_gateway_descriptions_are_named_as_an_uncounted_cost(self) -> None:
        """They arrive at connect too, and are fixed, so they are not summed."""

        app = _app()
        step = inspection.connect_step(app.surface.tree, instructions.build(app.surface.tree))

        assert step.aside is not None
        self.assertIn("gateway", step.aside)


class RefusalTests(unittest.TestCase):
    """A wrong ref is the most useful thing this command prints."""

    def test_a_bad_ref_prints_the_sentence_the_agent_would_read(self) -> None:
        app = _app()
        step = inspection.open_step(app.surface.tree, "platform/nope")

        self.assertTrue(step.refused)
        self.assertIn("holds no member named 'nope'", step.body)
        self.assertIn(OPEN_TOOL, step.body)
        self.assertIsNone(step.payload)

    def test_a_refusal_is_still_costed(self) -> None:
        app = _app()
        step = inspection.open_step(app.surface.tree, "platform/nope")

        self.assertGreater(step.cost.tokens, 0)

    def test_a_refused_step_is_a_finding_and_not_a_crash(self) -> None:
        app = _app()
        traced = inspection.trace(
            app.surface.tree, ["platform", "platform/nope"], instructions="x"
        )

        self.assertEqual(len(traced.steps), 4)
        self.assertEqual(len(traced.failures), 2)  # the bad ref, and "x"

    def test_reading_something_that_is_not_content_is_refused_in_words(
        self,
    ) -> None:
        app = _app()
        step = inspection.read_step(app.surface.tree, "platform/responder/diagnose")

        self.assertTrue(step.refused)
        self.assertIn("names a skill", step.body)


class RoutingSentenceTests(unittest.TestCase):
    """The one disclosure rule nothing refuses, measured rather than enforced.

    A description answers "should I go here" and never "what will I find
    inside". `core` cannot police that: past "not empty" the rule is about
    wording, and a framework with an opinion about English would be wrong in
    Chinese and wrong again in a Go port. So it is reported here, to the
    developer who can act on it, and nothing is blocked.
    """

    def test_the_bundled_declaration_passes_its_own_rule(self) -> None:
        step = inspection.open_step(Disclosure.of(Platform), "platform")

        for check in step.checks:
            with self.subTest(note=check.note):
                self.assertTrue(check.ok, check.note)

    def test_a_sentence_that_describes_the_inside_is_reported(self) -> None:
        role = Role(
            name="r",
            description="A role.",
            instructions="Go.",
            skills=[Skill(name="diagnose", description="A procedure.", instructions="Go.")],
            tools=[Tool(name="get_logs", description="Read the diagnose output.")],
        )

        step = inspection.open_step(Disclosure.of(role), "r")
        failed = [check for check in step.checks if not check.ok]

        self.assertEqual(len(failed), 1)
        self.assertIn("get_logs", failed[0].note)

    def test_a_routing_sentence_that_runs_long_is_reported(self) -> None:
        role = Role(
            name="r",
            description="A role.",
            instructions="Go.",
            tools=[Tool(name="wordy", description="A capability. " * 40)],
        )

        step = inspection.open_step(Disclosure.of(role), "r")
        failed = [check for check in step.checks if not check.ok]

        self.assertEqual(len(failed), 1)
        self.assertIn("wordy", failed[0].note)

    def test_nothing_is_reported_for_a_node_that_holds_nothing(self) -> None:
        """A leaf has no cards to check, so it makes no claim either way."""

        step = inspection.open_step(Disclosure.of(Platform), "platform/responder/diagnose")

        self.assertEqual(step.checks, ())


class SweepTests(unittest.TestCase):
    """`--all`: every node once, and every ref it yields actually resolves."""

    def test_every_ref_resolves(self) -> None:
        app = _app()
        for ref in inspection.every_ref(app.surface.tree):
            with self.subTest(ref=ref):
                app.surface.tree.find(ref)

    def test_every_node_appears_exactly_once(self) -> None:
        app = _app()
        refs = list(inspection.every_ref(app.surface.tree))

        self.assertEqual(len(refs), len(set(refs)))
        self.assertEqual(
            set(refs),
            {
                "platform",
                "platform/responder",
                "platform/responder/diagnose",
                "platform/responder/get_pod_logs",
                "platform/responder/runbook",
            },
        )

    def test_a_sub_role_is_listed_as_a_role_and_not_again_as_a_member(
        self,
    ) -> None:
        """The bug this guards: every deep branch listed twice over."""

        refs = list(inspection.every_ref(_app().surface.tree))

        self.assertEqual(refs.count("platform/responder"), 1)


class ReadTests(unittest.TestCase):
    def test_a_resource_is_read_only_when_asked_for(self) -> None:
        app = _app()
        without = inspection.trace(
            app.surface.tree, ["platform/responder/runbook"], instructions="x"
        )
        with_read = inspection.trace(
            app.surface.tree, ["platform/responder/runbook"], instructions="x", read=True
        )

        self.assertNotIn("RUNBOOK-BODY", without.steps[-1].body)
        self.assertIn("RUNBOOK-BODY", with_read.steps[-1].body)

    def test_the_open_step_says_what_it_is_not_showing(self) -> None:
        app = _app()
        step = inspection.open_step(app.surface.tree, "platform/responder/runbook")

        assert step.aside is not None
        self.assertIn("--read", step.aside)


class CostTests(unittest.TestCase):
    def test_a_wide_character_is_not_costed_as_a_quarter_token(self) -> None:
        """A declaration written in Chinese must not be reported at a quarter.

        The reason this file does not use `len(text) / 4`: it would have said
        this instructions block costs 4 tokens.
        """

        cost = inspection.Cost.of("先读状态，再读日志。")

        self.assertEqual(cost.characters, 10)
        self.assertEqual(cost.bytes, 30)
        self.assertGreaterEqual(cost.tokens, 10)

    def test_the_total_is_the_sum_of_the_steps(self) -> None:
        app = _app()
        traced = inspection.trace(
            app.surface.tree, ["platform", "platform/responder"], instructions="hello"
        )

        self.assertEqual(
            traced.total.characters,
            sum(step.cost.characters for step in traced.steps),
        )


class RenderTests(unittest.TestCase):
    def _trace(self) -> inspection.Trace:
        app = _app()
        return inspection.trace(
            app.surface.tree,
            ["platform", "platform/responder"],
            instructions=instructions.build(app.surface.tree),
        )

    def test_the_default_rendering_shows_the_payloads(self) -> None:
        rendered = inspection.render(self._trace())

        self.assertIn("input_schema", rendered)
        self.assertIn("step 0", rendered)
        self.assertIn(inspection.CONNECT, rendered)

    def test_the_summary_rendering_shows_costs_without_payloads(self) -> None:
        rendered = inspection.render(self._trace(), payloads=False)

        self.assertNotIn("input_schema", rendered)
        self.assertIn("running", rendered)

    def test_the_json_rendering_parses_and_keeps_every_step(self) -> None:
        traced = self._trace()
        parsed = json.loads(inspection.as_json(traced))

        self.assertEqual(len(parsed["steps"]), len(traced.steps))
        self.assertEqual(
            parsed["total"]["estimated_tokens"], traced.total.tokens
        )
        self.assertEqual(parsed["steps"][0]["call"], inspection.CONNECT)


if __name__ == "__main__":
    unittest.main()
