"""The four calls an agent makes, exercised without a wire.

**No MCP SDK is imported here, and that is the point of the module.** Until
ADR 014 the only place the whole sequence could be observed was
`test_stdio_server.py`, which launches a subprocess and needs the SDK
installed; a checkout without it could not run a single assertion about what an
agent actually receives. The behaviour never depended on the wire — resolution,
levels, the two doors and every refusal are the kernel's — so the tests do not
either, and the wire-level suite is left with the claims that genuinely need a
wire: that the surface really is these four names, that the read-only hint is
attached, and that nothing but protocol reaches stdout.

The trace below is the one `docs/verification/hosts.md` records a host taking.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from contexture.core.constants import (
    DISCOVER_TOOL,
    INSPECT_TOOL,
    INVOKE_READ_ONLY_TOOL,
    INVOKE_TOOL,
    OPEN_TOOL,
)
from contexture.core.errors import LookupFailure, NodeNotFoundError
from contexture.core.model.system_api import (
    GATEWAY,
    GATEWAY_TOOLS,
    Refused,
    SystemAPI,
    unresolved,
    wrong_door,
)
from contexture.core.model.tool import Tool
from contexture.core.model.disclosure import Disclosure
from contexture.demo import fixtures
from contexture.demo.role import KubernetesPlatform

ROOT = "kubernetes-platform"
RESPONSE = f"{ROOT}/incident-response"
DIAGNOSE = f"{RESPONSE}/diagnose-crash-loop-backoff"
ROLLBACK = f"{ROOT}/deployment-ops/roll-back-a-failed-release"
STATUS = f"{RESPONSE}/get_pod_status"
LOGS = f"{RESPONSE}/get_pod_logs"
RUNBOOK = f"{RESPONSE}/crash_loop_runbook"
WRITE = f"{ROOT}/deployment-ops/roll_back_deployment"

#: A line from the diagnosis procedure. It must not appear before step 4.
PROCEDURE = "Do not recommend restarting or deleting the Pod"

POD = {"namespace": fixtures.NAMESPACE, "pod": fixtures.POD}


def _api(*, bind: object = None, **kwargs: object) -> SystemAPI:
    """The demo, bound so that a card's schema says where it came from."""

    tree = Disclosure.of(
        KubernetesPlatform, bind=_Derived if bind is None else bind
    )
    return SystemAPI(tree=tree, **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class _Derived:
    """A binding whose schema names the tool it was derived for."""

    tool: object

    @property
    def schema(self) -> dict[str, object]:
        return {"derived_for": self.tool.name}  # type: ignore[attr-defined]

    async def call(self, arguments: object, context: object = None) -> object:
        return await self.tool.invoke(**(arguments or {}))  # type: ignore[attr-defined]


class TraceTests(unittest.IsolatedAsyncioTestCase):
    """One session, one level per call, with what each call may not carry."""

    async def test_1_discover_answers_with_the_roots_and_nothing_under_them(
        self,
    ) -> None:
        """The cost of entering is the number of roots, not the forest."""

        payload = await _api().discover()

        self.assertEqual(
            [card["ref"] for card in payload["roles"]], [ROOT]
        )
        self.assertEqual(payload["skills"], [])
        self.assertEqual(payload["tools"], [])
        # The second level is a separate call. Nothing below a root is named,
        # and no procedure and no schema rides along.
        self.assertNotIn("incident-response", str(payload))
        self.assertNotIn("derived_for", str(payload))
        self.assertNotIn(PROCEDURE, str(payload))

    async def test_2_opening_the_root_delivers_orchestration_and_cards(
        self,
    ) -> None:
        """A coordinating role owns no tools; its instructions are the routing."""

        payload = await _api().open(ROOT)

        self.assertIn("Diagnose\nbefore remediating", payload["instructions"])
        self.assertEqual(
            [card["ref"] for card in payload["roles"]],
            [RESPONSE, f"{ROOT}/deployment-ops"],
        )
        # Two sub-roles hold seven capabilities between them, and this call
        # pays for none of them.
        self.assertEqual(payload["tools"], [])
        self.assertNotIn("get_pod_status", str(payload))

    async def test_inspect_compares_candidates_without_activating_them(self) -> None:
        api = _api()

        payload = await api.inspect([RESPONSE, f"{ROOT}/deployment-ops"])

        self.assertEqual(
            [item["node"]["ref"] for item in payload["items"]],
            [RESPONSE, f"{ROOT}/deployment-ops"],
        )
        self.assertIn("candidate evaluation", payload["notice"])
        self.assertNotIn('"instructions":', json.dumps(payload))
        self.assertNotIn("input_schema", str(payload))
        self.assertNotIn("read_only", str(payload))
        self.assertNotIn(PROCEDURE, str(payload))
        self.assertEqual(
            [card["ref"] for card in payload["items"][0]["members"]["skills"]],
            [DIAGNOSE],
        )
        self.assertEqual(
            set(payload["items"][0]["members"]["tools"][0]),
            {"kind", "name", "description", "ref"},
        )

    async def test_inspect_requires_an_atomic_unique_bounded_batch(self) -> None:
        api = _api()
        for refs in ([], [RESPONSE, RESPONSE], [RESPONSE] * 33, [" "]):
            with self.subTest(refs=len(refs)), self.assertRaises(Refused):
                await api.inspect(refs)

    async def test_3_opening_a_specialism_delivers_callable_tool_cards(
        self,
    ) -> None:
        """The first call that carries a schema, and still not a procedure."""

        payload = await _api().open(RESPONSE)

        cards = {card["name"]: card for card in payload["tools"]}
        self.assertEqual(
            sorted(cards),
            ["crash_loop_runbook", "get_pod_events", "get_pod_logs", "get_pod_status"],
        )
        self.assertEqual(cards["get_pod_status"]["ref"], STATUS)
        self.assertTrue(cards["get_pod_status"]["read_only"])
        self.assertEqual(
            cards["get_pod_status"]["input_schema"], {"derived_for": "get_pod_status"}
        )
        self.assertEqual([card["ref"] for card in payload["skills"]], [DIAGNOSE])
        self.assertNotIn(PROCEDURE, str(payload))

    async def test_4_only_opening_the_skill_delivers_the_procedure(self) -> None:
        """There is no second, shorter copy of this text anywhere."""

        payload = await _api().open(DIAGNOSE)

        self.assertIn(PROCEDURE, payload["instructions"])
        self.assertIn("get_pod_status", payload["instructions"])

    async def test_5_a_read_only_tool_runs_through_the_read_only_door(
        self,
    ) -> None:
        api = _api()

        status = await api.invoke_read_only(STATUS, POD)
        logs = await api.invoke_read_only(LOGS, POD)
        runbook = await api.invoke_read_only(RUNBOOK)

        self.assertEqual(status.container_state, "CrashLoopBackOff")
        self.assertEqual(status.restart_count, 14)
        self.assertIn("DB_URL is missing", logs)
        # Content is an argument-less read-only tool and nothing else, which is
        # why it needs no second kind of node to hold it.
        self.assertIn("CrashLoopBackOff", runbook)

    async def test_a_write_runs_only_through_the_writing_door(self) -> None:
        result = await _api().invoke(
            WRITE, {"namespace": fixtures.NAMESPACE, "deployment": "payments-api"}
        )

        self.assertIn("Rolled", result)


class DoorTests(unittest.IsolatedAsyncioTestCase):
    """The one protection the whole surface rests on.

    A host decides whether to involve a human from the hint on the entry point.
    If a write could be run through the read-only door, that decision would
    have been made about the wrong call — so a mismatch is refused rather than
    honoured, in both directions.
    """

    async def test_a_write_is_refused_at_the_read_only_door(self) -> None:
        with self.assertRaises(Refused) as caught:
            await _api().invoke_read_only(WRITE, {"namespace": "prod"})

        self.assertIn(INVOKE_TOOL, str(caught.exception))

    async def test_a_read_is_refused_at_the_writing_door(self) -> None:
        with self.assertRaises(Refused) as caught:
            await _api().invoke(LOGS, POD)

        self.assertIn(INVOKE_READ_ONLY_TOOL, str(caught.exception))

    async def test_the_refusal_happens_before_the_tool_runs(self) -> None:
        """Refusing after the fact would be no protection at all."""

        ran: list[str] = []

        @dataclass(frozen=True, slots=True)
        class Recording:
            tool: Tool

            @property
            def schema(self) -> dict[str, object]:
                return {}

            async def call(self, arguments: object, context: object = None) -> str:
                ran.append(self.tool.name)
                return "ran"

        with self.assertRaises(Refused):
            await _api(bind=Recording).invoke_read_only(WRITE, {})

        self.assertEqual(ran, [])


class WrongRefTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_wrong_ref_says_what_the_role_holds_and_what_to_call(
        self,
    ) -> None:
        """A wrong ref is recoverable, so the reply is worth spending words on."""

        with self.assertRaises(Refused) as caught:
            await _api().open(f"{ROOT}/incidents")

        rendered = str(caught.exception)
        self.assertIn("incidents", rendered)
        self.assertIn("incident-response", rendered)
        self.assertIn("deployment-ops", rendered)
        self.assertIn(OPEN_TOOL, rendered)

    async def test_running_something_that_is_not_a_tool_names_the_right_call(
        self,
    ) -> None:
        with self.assertRaises(Refused) as caught:
            await _api().invoke_read_only(DIAGNOSE)

        self.assertIn(OPEN_TOOL, str(caught.exception))


class ReferenceTests(unittest.IsolatedAsyncioTestCase):
    """A procedure naming a capability it does not own.

    The demo's rollback procedure cannot perform the diagnosis it depends on —
    that belongs to another role — so it names it. This is the whole of the
    reference face: it produces no depth, no new address, and nothing to
    traverse.
    """

    async def test_a_named_capability_arrives_as_a_card(self) -> None:
        payload = await _api().open(ROLLBACK)

        (card,) = payload["uses"]
        self.assertEqual(card["ref"], DIAGNOSE)
        self.assertEqual(card["kind"], "skill")

    async def test_the_card_never_carries_the_procedure_it_names(self) -> None:
        """At ROUTE, and this is what makes a reference cycle terminate.

        Rendering a referenced skill at ACTIVE would make `a -> b -> a`
        unbounded; rendering it as a card makes it two cards naming each other.
        """

        payload = await _api().open(ROLLBACK)

        self.assertEqual(set(payload["uses"][0]), {"kind", "name", "description", "ref"})
        self.assertNotIn(PROCEDURE, str(payload))

    async def test_a_reference_that_resolves_to_nothing_never_reaches_here(
        self,
    ) -> None:
        """It is refused when the tree is sealed, not when somebody reaches it.

        A skill is constructed long before the branch it names exists, so the
        binding is late by necessity; sealing is the earliest moment it can be
        checked, and it is checked there so that a broken procedure fails on
        the way up rather than in front of a user.
        """

        from contexture.core.errors import ModelValidationError
        from contexture.core.model.role import Role
        from contexture.core.model.skill import Skill

        broken = Role(
            name="r",
            description="A role.",
            instructions="Go.",
            skills=[
                Skill(
                    name="s",
                    description="A procedure.",
                    instructions="Go.",
                    uses=("r/nowhere",),
                )
            ],
        )

        with self.assertRaises(ModelValidationError):
            Disclosure.of(broken)


class SignpostTests(unittest.TestCase):
    """What a direct arrival is owed, and what it is deliberately not told."""

    def test_a_direct_arrival_is_told_how_many_siblings_it_skipped(self) -> None:
        levels = _api().tree.index.signpost(DIAGNOSE)

        self.assertEqual(levels, ((ROOT, 2), (RESPONSE, 0)))

    def test_it_never_names_them(self) -> None:
        """Naming them would re-buy the level the direct arrival just saved."""

        rendered = str(_api().tree.index.signpost(DIAGNOSE))

        self.assertNotIn("deployment-ops", rendered)


class ReservedTests(unittest.IsolatedAsyncioTestCase):
    """A node a person has claimed, and the two doors that disagree about it."""

    async def test_a_model_is_refused_and_told_to_name_the_command(self) -> None:
        with self.assertRaises(Refused) as caught:
            await _api(reserved=frozenset({ROLLBACK})).open(ROLLBACK)

        rendered = str(caught.exception)
        self.assertIn("opened by a person", rendered)
        self.assertIn("tell the user which command runs it", rendered)

    async def test_a_person_reaches_it_by_the_door_reserved_for_them(
        self,
    ) -> None:
        payload = await _api(reserved=frozenset({ROLLBACK})).open_for_a_person(
            ROLLBACK
        )

        self.assertIn("A rollback destroys the evidence", payload["instructions"])

    async def test_reserving_a_node_leaves_everything_else_reachable(self) -> None:
        api = _api(reserved=frozenset({ROLLBACK}))

        self.assertIn(PROCEDURE, (await api.open(DIAGNOSE))["instructions"])

    async def test_nothing_is_reserved_by_default(self) -> None:
        self.assertIn(
            "A rollback destroys the evidence",
            (await _api().open(ROLLBACK))["instructions"],
        )


class StatelessnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.api = _api()

    async def test_an_answer_does_not_depend_on_what_was_asked_before(
        self,
    ) -> None:
        """The protocol forbids a surface that varies with an earlier call.

        Asserted by asking twice with a whole traversal in between: whatever
        the API is holding, it is not a memory of where this session has been.
        """

        first = await self.api.open(RESPONSE)
        await self.api.discover()
        await self.api.open(DIAGNOSE)
        await self.api.invoke_read_only(STATUS, POD)

        self.assertEqual(await self.api.open(RESPONSE), first)


class SurfaceVocabularyTests(unittest.TestCase):
    def test_the_gateway_is_five_entry_points_stated_once(self) -> None:
        self.assertEqual(len(GATEWAY), 5)
        self.assertEqual(
            GATEWAY_TOOLS,
            (
                DISCOVER_TOOL, INSPECT_TOOL, OPEN_TOOL,
                INVOKE_READ_ONLY_TOOL, INVOKE_TOOL,
            ),
        )

    def test_exactly_one_entry_point_is_not_read_only(self) -> None:
        writing = [entry.name for entry in GATEWAY if not entry.read_only]

        self.assertEqual(writing, [INVOKE_TOOL])

    def test_every_entry_point_describes_itself(self) -> None:
        for entry in GATEWAY:
            with self.subTest(entry=entry.name):
                self.assertGreater(len(entry.description), 80)

    def test_only_the_call_that_delivers_schemas_talks_about_them(self) -> None:
        """The description an agent reads has to match what arrives.

        `contexture_open` is where a tool's schema comes from, so it is the
        only description that may promise one.
        """

        described = {entry.name: entry.description for entry in GATEWAY}

        self.assertIn("schema", described[OPEN_TOOL])
        self.assertNotIn("schema", described[DISCOVER_TOOL])
        self.assertNotIn("schema", described[INSPECT_TOOL])


class UnresolvedTests(unittest.TestCase):
    def _failure(self, reason: LookupFailure) -> NodeNotFoundError:
        return NodeNotFoundError(
            reason=reason,
            ref="team/troubleshooter/banana",
            segment="banana",
            scope="troubleshooter",
            kind="skill",
            wanted="tool",
            known=("diagnose", "get_pod_logs"),
        )

    def test_every_lookup_failure_has_a_rendering(self) -> None:
        """A reason with no branch is a failure an agent is told nothing about."""

        for reason in LookupFailure:
            with self.subTest(reason=reason.value):
                rendered = unresolved(self._failure(reason))
                self.assertNotIn("could not be resolved", rendered)
                self.assertGreater(len(rendered), 40)

    def test_every_rendering_names_the_call_that_recovers_from_it(self) -> None:
        """The half a wrong ref most needs is what to do next.

        This is the sentence that could not be written beside the failure until
        the entry points became the kernel's own. It can be now, and this is
        what that bought.
        """

        for reason in LookupFailure:
            with self.subTest(reason=reason.value):
                rendered = unresolved(self._failure(reason))
                self.assertTrue(
                    any(name in rendered for name in GATEWAY_TOOLS),
                    f"{reason.value} leaves the agent with no next call",
                )

    def test_a_missing_member_says_what_the_role_does_hold(self) -> None:
        rendered = unresolved(self._failure(LookupFailure.NO_SUCH_MEMBER))

        self.assertIn("banana", rendered)
        self.assertIn("diagnose", rendered)
        self.assertIn("get_pod_logs", rendered)

    def test_an_empty_role_is_reported_as_empty_rather_than_as_a_blank_list(
        self,
    ) -> None:
        rendered = unresolved(
            NodeNotFoundError(
                reason=LookupFailure.NO_SUCH_MEMBER,
                ref="team/empty/x",
                segment="x",
                scope="empty",
            )
        )

        self.assertIn("holds nothing", rendered)


class WrongDoorTests(unittest.TestCase):
    def test_each_door_names_the_other_one(self) -> None:
        self.assertIn(
            INVOKE_READ_ONLY_TOOL, wrong_door("a/b", is_read_only=True)
        )
        self.assertIn(INVOKE_TOOL, wrong_door("a/b", is_read_only=False))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
