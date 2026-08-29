"""Tests for the navigation model: what is disclosed, when, and what it costs.

The claim these defend is narrow and load-bearing. The role skeleton is cheap
enough to hand over whole; everything else waits until the role holding it is
opened; and nothing an agent can see is something it cannot then open.
"""

from __future__ import annotations

import json
import unittest

from contexture.core.errors import (
    LookupFailure,
    ModelValidationError,
    NodeNotFoundError,
)
from contexture.core.model.role import Role
from contexture.core.model.skill import Skill
from contexture.core.model.tool import Tool
from contexture.core.model.disclosure import Disclosure
from contexture.core.model.index import Index

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from serving import Marked  # noqa: E402

PROCEDURE = "Read the status, then the logs, then the events."


class BoundaryTests(unittest.TestCase):
    """Disclosure discloses; the Index answers what is there.

    The bug this pins is the one the whole refactor exists to prevent coming
    back: a view that forwards every question to the thing underneath it is a
    shell, not a layer. Traversing the address space is the index's job, and a
    consumer that wants it reaches `view.index` rather than finding the method
    re-exposed here. If one of these ever grows a forwarding method on
    `Disclosure`, this fails and names it.
    """

    #: Everything that is a fact *about* the forest — where a node is, what one
    #: of a kind there is, how a half-typed ref ranks. Index's, never the view's.
    INDEX_ONLY = (
        "walk",
        "nodes_with_refs",
        "of_kind",
        "parent_of",
        "children_of",
        "matching_refs",
        "signpost",
        "roles_by_level",
        "roles_with_refs",
        "crossings",
        "binding_of",
    )

    def test_every_traversal_lives_on_the_index_and_not_the_view(self) -> None:
        for name in self.INDEX_ONLY:
            with self.subTest(method=name):
                self.assertTrue(
                    hasattr(Index, name), f"Index should answer {name!r}"
                )
                self.assertFalse(
                    hasattr(Disclosure, name),
                    f"Disclosure re-exposes {name!r}; reach view.index instead.",
                )

    def test_the_view_holds_the_index_it_discloses(self) -> None:
        view = _tree()
        self.assertIsInstance(view.index, Index)
        self.assertIs(view.registry, view.index)


class GetPodLogs(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="get_pod_logs",
            description="Return the recent container logs for a Pod.",
            read_only=True,
        )

    async def invoke(self, namespace: str, pod: str) -> str:
        return f"{namespace}/{pod}"


class DeletePod(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="delete_pod",
            description="Delete a Pod so its controller recreates it.",
        )

    async def invoke(self, namespace: str, pod: str) -> str:
        return "deleted"


class CrashLoopRunbook(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="crash_loop_runbook",
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
            instructions=PROCEDURE,
        )


class Troubleshooter(Role):
    def __init__(self) -> None:
        super().__init__(
            name="troubleshooter",
            description="Diagnose unhealthy Pods.",
            instructions="Inspect before changing anything.",
            skills=[Diagnose()],
            tools=[GetPodLogs(), CrashLoopRunbook()],
        )
class Operator(Role):
    def __init__(self) -> None:
        super().__init__(
            name="operator",
            description="Repair unhealthy Pods.",
            instructions="Ask before destroying anything.",
            tools=[DeletePod()],
        )


def _troubleshooter() -> Role:
    """A fresh troubleshooter each time, so one test cannot edit another's.

    Built rather than declared because these tests replace its procedures: a
    declared class is a shared object, and assigning to `Troubleshooter.skills`
    would change what every other test in this file is addressing.
    """

    return Role(
        name="troubleshooter",
        description="Diagnose unhealthy Pods.",
        instructions="Inspect before changing anything.",
        skills=[Diagnose()],
        tools=[GetPodLogs(), CrashLoopRunbook()],
    )


def _tree() -> Disclosure:
    team = Role(
        name="team",
        description="An engineering team.",
        instructions="Route to the right specialist.",
        children=[Troubleshooter(), Operator()],
    )
    return Disclosure.of(team, bind=Marked)


class SkeletonTests(unittest.TestCase):
    def test_the_skeleton_is_the_roots_and_stops_there(self) -> None:
        """One call shows one level of siblings — including the first one.

        Sub-roles arrive from opening the root, which an agent must do anyway
        to get its instructions, so entering this server costs the number of
        roots rather than the size of the forest.
        """

        cards = _tree().skeleton()["roles"]

        self.assertEqual([card["ref"] for card in cards], ["team"])
        self.assertTrue(all(card["kind"] == "role" for card in cards))

    def test_the_cost_of_entering_does_not_grow_with_depth(self) -> None:
        """The regression this exists to catch, stated as a number."""

        shallow = Role(
            name="team", description="A team.", instructions="Route.",
            children=[Troubleshooter(), Operator()],
        )
        deep = shallow
        for level in range(6):
            deep = Role(
                name=f"tier-{level}",
                description="A tier.",
                instructions="Route.",
                children=[deep],
            )

        self.assertEqual(
            len(Disclosure.of(shallow).skeleton()["roles"]),
            len(Disclosure.of(deep).skeleton()["roles"]),
        )

    def test_the_skeleton_carries_no_instructions_and_no_schemas(self) -> None:
        """A routing card is a name, a sentence and a path. Nothing else."""

        rendered = json.dumps(_tree().skeleton())

        self.assertNotIn(PROCEDURE, rendered)
        self.assertNotIn("Inspect before changing", rendered)
        self.assertNotIn("input_schema", rendered)
        self.assertNotIn("get_pod_logs", rendered)


    def test_the_skeleton_is_never_truncated_however_many_roots_there_are(
        self,
    ) -> None:
        """The one call that must always answer in full, and why.

        Every other budget in this project cuts and then names the call that
        restores what was cut. The roster's own truncation line names *this*
        call — "for the complete list" — so a cap here would turn that sentence
        into a lie with nothing left to point at.

        It also runs straight into ADR 004: the roots are one sibling set, and
        a choice made among a subset of the alternatives is a guess rather than
        a choice. Capping the entrance would buy characters by reintroducing
        the exact failure the whole disclosure model exists to remove.

        So this is a floor, not an oversight. Anyone tempted to add a budget
        here has to change the roster's promise first.
        """

        roots = [
            Role(
                name=f"root-{index}",
                description="A responsibility boundary with a realistic sentence "
                "attached, long enough that forty of them are not free.",
                instructions="Route to the sub-role that owns the failure.",
            )
            for index in range(40)
        ]

        skeleton = Disclosure.of(roots).skeleton()

        self.assertEqual(len(skeleton["roles"]), 40)
        self.assertTrue(all("ref" in card for card in skeleton["roles"]))


class CardTests(unittest.TestCase):
    def test_every_card_anywhere_carries_the_ref_that_opens_it(self) -> None:
        """A card without a ref is a dead end: it can be seen, not reached.

        Both paths are checked. Roles used to render their own members, which
        produced cards with no ref on the open path while the discover path
        looked correct.
        """

        tree = _tree()
        cards = list(tree.skeleton()["roles"])
        # Walk the forest the way an agent has to now: one level per call,
        # following only refs that were actually handed over.
        pending = [card["ref"] for card in cards]
        while pending:
            opened = tree.open(pending.pop())
            for group in ("roles", "skills", "tools"):
                cards.extend(opened[group])
            pending.extend(card["ref"] for card in opened["roles"])

        self.assertGreater(len(cards), 6)
        for card in cards:
            with self.subTest(card=card["name"]):
                self.assertIn("ref", card)
                self.assertEqual(tree.open(card["ref"])["ref"], card["ref"])


class OpenTests(unittest.TestCase):
    def test_opening_a_role_reveals_its_members_with_schemas(self) -> None:
        opened = _tree().open("team/troubleshooter")

        self.assertEqual(opened["instructions"], "Inspect before changing anything.")
        self.assertEqual([s["ref"] for s in opened["skills"]],
                         ["team/troubleshooter/diagnose"])
        by_ref = {tool["ref"]: tool for tool in opened["tools"]}
        tool = by_ref["team/troubleshooter/get_pod_logs"]
        self.assertTrue(tool["read_only"])
        self.assertEqual(tool["input_schema"], {"tool": "get_pod_logs"})

    def test_opening_a_role_does_not_recurse_into_sub_roles(self) -> None:
        opened = _tree().open("team")

        self.assertEqual(
            [card["ref"] for card in opened["roles"]],
            ["team/troubleshooter", "team/operator"],
        )
        self.assertNotIn("get_pod_logs", json.dumps(opened))

    def test_only_opening_the_skill_delivers_the_procedure(self) -> None:
        tree = _tree()

        self.assertNotIn(PROCEDURE, json.dumps(tree.skeleton()))
        self.assertNotIn(PROCEDURE, json.dumps(tree.open("team/troubleshooter")))
        self.assertIn(
            PROCEDURE,
            tree.open("team/troubleshooter/diagnose")["instructions"],
        )

    def test_opening_content_yields_its_card_and_not_its_body(self) -> None:
        """Content is a tool now, so opening one is opening a tool.

        The card is complete — a name, a sentence, a ref and a schema — and the
        document itself arrives only when the tool is run.
        """

        opened = _tree().open("team/troubleshooter/crash_loop_runbook")

        self.assertTrue(opened["read_only"])
        self.assertNotIn("RUNBOOK-BODY", json.dumps(opened))


class NameTests(unittest.TestCase):
    """A card that can be seen must be openable — from both directions.

    `_card` takes its ref as an argument so a card cannot exist without one.
    These cover the other half: a ref that exists but addresses nothing,
    because a name ate the separator.
    """

    def _role(self, name: str, **members: object) -> Role:
        return Role(
            name=name,
            description=f"{name}.",
            instructions="Anything.",
            **members,  # type: ignore[arg-type]
        )

    def test_a_role_name_may_not_contain_the_separator(self) -> None:
        with self.assertRaises(ModelValidationError) as caught:
            Disclosure.of(self._role("a/b"))

        self.assertIn("/", str(caught.exception))

    def test_a_nested_role_name_may_not_contain_the_separator(self) -> None:
        """The check has to walk, not just look at the roots."""

        deep = self._role("root", children=[
            self._role("middle", children=[self._role("a/b")])
        ])

        with self.assertRaises(ModelValidationError):
            Disclosure.of(deep)

    def test_a_member_name_may_not_contain_the_separator_either(self) -> None:
        """Every kind is a ref segment, not just roles."""

        class Weird(Tool):
            """A tool whose name would split its own ref."""
            def __init__(self) -> None:
                        super().__init__(
                                    name="get/logs",
                                    description="A tool whose name would split its own ref.",
                        )

            async def invoke(self) -> str:
                return "x"

        with self.assertRaises(ModelValidationError) as caught:
            Disclosure.of(self._role("r", tools=[Weird()]))

        self.assertIn("tool", str(caught.exception))


class DepthTests(unittest.TestCase):
    """Nesting is recursive, and disclosure stays lazy at every level."""

    def _tower(self) -> Role:
        class Deep(Skill):
            """A skill four levels down."""
            def __init__(self) -> None:
                        super().__init__(
                                    name="deep",
                                    description="A skill four levels down.",
                                    instructions="PROCEDURE-AT-DEPTH",
                        )

        role = Role(
            name="l4",
            description="Level four.",
            instructions="Anything.",
            skills=[Deep()],
        )
        for name in ("l3", "l2", "l1"):
            role = Role(
                name=name,
                description=f"Level {name}.",
                instructions="Anything.",
                children=[role],
            )
        return role

    def test_the_skeleton_shows_only_the_root_however_deep_the_tower(self) -> None:
        tree = Disclosure.of(self._tower())

        self.assertEqual([c["ref"] for c in tree.skeleton()["roles"]], ["l1"])
        self.assertEqual(
            [ref for ref, _ in tree.index.roles_with_refs()],
            ["l1", "l1/l2", "l1/l2/l3", "l1/l2/l3/l4"],
        )

    def test_a_breadth_first_walk_finishes_a_level_before_descending(self) -> None:
        """Ordering matters wherever the walk gets cut off by a budget."""

        wide = Role(
            name="root", description="Root.", instructions="Route.",
            children=[
                Role(name=f"a{i}", description="A.", instructions="Route.",
                     children=[Role(name=f"a{i}-{j}", description="B.",
                                    instructions="Route.") for j in range(2)])
                for i in range(3)
            ],
        )
        refs = [ref for ref, _ in Disclosure.of(wide).index.roles_by_level()]

        self.assertEqual(refs[:4], ["root", "root/a0", "root/a1", "root/a2"])

    def test_opening_a_role_never_recurses_past_its_own_children(self) -> None:
        """Each level costs one call. That is the whole point of the tree."""

        tree = Disclosure.of(self._tower())

        for ref, expected in (
            ("l1", ["l1/l2"]),
            ("l1/l2", ["l1/l2/l3"]),
            ("l1/l2/l3", ["l1/l2/l3/l4"]),
            ("l1/l2/l3/l4", []),
        ):
            with self.subTest(ref=ref):
                opened = tree.open(ref)
                self.assertEqual(
                    [card["ref"] for card in opened["roles"]], expected
                )

    def test_a_procedure_four_levels_down_arrives_only_when_opened(self) -> None:
        tree = Disclosure.of(self._tower())

        for ref in ("l1", "l1/l2", "l1/l2/l3", "l1/l2/l3/l4"):
            with self.subTest(ref=ref):
                self.assertNotIn("PROCEDURE-AT-DEPTH", json.dumps(tree.open(ref)))

        self.assertIn(
            "PROCEDURE-AT-DEPTH",
            json.dumps(tree.open("l1/l2/l3/l4/deep")),
        )


class ResolutionTests(unittest.TestCase):
    """The tree reports *facts* about a failed lookup.

    It does not write the sentence an agent reads: that needs the gateway tool
    names, which live a layer up. These assert the facts are complete enough
    for that sentence to be written — see `test_messages` for the rendering.
    """

    def test_an_unknown_member_reports_what_the_role_does_hold(self) -> None:
        with self.assertRaises(NodeNotFoundError) as caught:
            _tree().find("team/troubleshooter/banana")

        failure = caught.exception
        self.assertIs(failure.reason, LookupFailure.NO_SUCH_MEMBER)
        self.assertEqual(failure.segment, "banana")
        self.assertEqual(failure.scope, "troubleshooter")
        self.assertIn("get_pod_logs", failure.known)
        self.assertIn("diagnose", failure.known)

    def test_a_failed_lookup_collects_the_whole_reference_on_its_way_up(
        self,
    ) -> None:
        """A role knows its own name; only the tree knows the path walked."""

        with self.assertRaises(NodeNotFoundError) as caught:
            _tree().find("team/troubleshooter/banana")

        self.assertEqual(caught.exception.ref, "team/troubleshooter/banana")

    def test_an_unknown_root_reports_the_roots_that_exist(self) -> None:
        with self.assertRaises(NodeNotFoundError) as caught:
            _tree().find("nobody")

        failure = caught.exception
        self.assertIs(failure.reason, LookupFailure.NO_SUCH_ROOT)
        self.assertIn("team", failure.known)

    def test_a_reference_may_not_continue_past_a_leaf(self) -> None:
        with self.assertRaises(NodeNotFoundError) as caught:
            _tree().find("team/troubleshooter/diagnose/deeper")

        failure = caught.exception
        self.assertIs(failure.reason, LookupFailure.NOT_A_CONTAINER)
        self.assertEqual(failure.kind, "skill")

    def test_a_lookup_failure_carries_no_prose(self) -> None:
        """The two audiences get two renderings, so neither is baked in here."""

        with self.assertRaises(NodeNotFoundError) as caught:
            _tree().find("nobody")

        for tool_name in ("contexture_discover", "contexture_open"):
            self.assertNotIn(tool_name, str(caught.exception))

    def test_the_typed_accessors_say_what_the_ref_actually_names(self) -> None:
        tree = _tree()

        with self.assertRaises(NodeNotFoundError) as caught:
            tree.tool("team/troubleshooter/diagnose")
        self.assertIs(caught.exception.reason, LookupFailure.WRONG_KIND)
        self.assertEqual(caught.exception.kind, "skill")
        self.assertEqual(caught.exception.wanted, "tool")

        with self.assertRaises(NodeNotFoundError) as caught:
            tree.tool("team/troubleshooter")
        self.assertEqual(caught.exception.kind, "role")
        self.assertEqual(caught.exception.wanted, "tool")

    def test_resolution_does_not_depend_on_earlier_calls(self) -> None:
        """The surface is stateless, so traversal has to be too."""

        tree = _tree()
        first = tree.open("team/troubleshooter")
        tree.open("team/operator")

        self.assertEqual(tree.open("team/troubleshooter"), first)


class ConstructionTests(unittest.TestCase):
    def test_one_root_or_many_are_both_accepted(self) -> None:
        single = Disclosure.of(Troubleshooter)
        several = Disclosure.of([Troubleshooter, Operator])

        self.assertEqual(len(single.roots), 1)
        self.assertEqual(len(several.roots), 2)

    def test_an_empty_forest_is_rejected(self) -> None:
        with self.assertRaises(ModelValidationError):
            Disclosure.of([])

    def test_two_roots_may_not_share_a_name(self) -> None:
        with self.assertRaises(ModelValidationError):
            Disclosure.of([Troubleshooter, Troubleshooter])

    def test_a_cycle_is_rejected_when_the_forest_is_built(self) -> None:
        """A cycle is only visible once the whole forest is in hand."""

        parent = Role(name="parent", description="A role.", instructions="Go.")
        child = Role(
            name="child",
            description="A role.",
            instructions="Go.",
            children=[parent],
        )
        parent.children.append(child)

        with self.assertRaises(ModelValidationError):
            Disclosure.of(parent)


if __name__ == "__main__":
    unittest.main()


class ReferenceTests(unittest.TestCase):
    """`uses`: naming a capability that lives somewhere else.

    Containment gives a node its address and so may happen once; reference
    consumes an address that already exists and so may happen any number of
    times. Everything below is a consequence of that one distinction.
    """

    @staticmethod
    def _with_uses(*refs: str) -> Disclosure:
        # The declared members are what the other tests address; the one built
        # here is the reference this helper exists to make, and its `uses` is
        # different on every call. Classes and instances sit in one list on
        # purpose: whichever way a member arrives, the manager is what turns it
        # into a node.
        troubleshooter = Role(
            name="troubleshooter",
            description="Diagnose unhealthy Pods.",
            instructions="Inspect before changing anything.",
            skills=[
                Diagnose(),
                Skill(
                    name="triage",
                    description="Decide whether a Pod is worth repairing.",
                    instructions="Read the logs, then decide.",
                    uses=refs,
                ),
            ],
            tools=[GetPodLogs(), CrashLoopRunbook()],
        )
        team = Role(
            name="team",
            description="An engineering team.",
            instructions="Route to the right specialist.",
            children=[troubleshooter, Operator()],
        )
        return Disclosure.of(team, bind=Marked)

    def test_every_addressable_node_is_enumerated_not_only_roles(self) -> None:
        """What completion offers and what `uses` is checked against.

        `roles_with_refs` answers "what roles are there". A person completing a
        command argument, and a procedure naming a step, both want the fuller
        question — a skill or a tool is exactly what either is reaching for.
        """

        refs = {ref for ref, _ in _tree().index.nodes_with_refs()}

        self.assertIn("team", refs)
        self.assertIn("team/troubleshooter", refs)
        self.assertIn("team/troubleshooter/diagnose", refs)
        self.assertIn("team/troubleshooter/get_pod_logs", refs)
        self.assertIn("team/troubleshooter/crash_loop_runbook", refs)
        self.assertIn("team/operator/delete_pod", refs)

    def test_a_reference_reaches_a_sibling_branch_containment_cannot(self) -> None:
        """The whole point: a procedure naming what it does not own."""

        tree = self._with_uses("team/operator/delete_pod")
        opened = tree.open("team/troubleshooter/triage")

        (card,) = opened["uses"]
        self.assertEqual(card["ref"], "team/operator/delete_pod")
        self.assertEqual(card["kind"], "tool")
        # The tool is not a member of the role that names it, and its own ref
        # is unchanged: reference consumed the address, it did not mint one.
        self.assertNotIn(
            "delete_pod",
            [member["name"] for member in tree.open("team/troubleshooter")["tools"]],
        )

    def test_a_referenced_tool_arrives_callable(self) -> None:
        """A card the agent has to go and complete is a card that cost a turn."""

        card, = self._with_uses("team/operator/delete_pod").open(
            "team/troubleshooter/triage"
        )["uses"]

        self.assertIn("input_schema", card)
        self.assertIn("read_only", card)

    def test_a_reference_card_never_carries_its_own_references(self) -> None:
        """The invariant that makes a reference cycle harmless.

        Cards render at ROUTE, so the server answers one level and stops. Were
        this to expand, `A -> B -> A` would be unbounded rather than two cards
        naming each other.
        """

        tree = self._with_uses("team/troubleshooter/diagnose")
        card, = tree.open("team/troubleshooter/triage")["uses"]

        self.assertNotIn("uses", card)
        self.assertNotIn("instructions", card)

    def test_two_procedures_may_name_the_same_capability(self) -> None:
        """Reference is not exclusive, because it creates no address."""

        troubleshooter = _troubleshooter()
        troubleshooter.skills = [
            Skill(
                name=name,
                description="A procedure.",
                instructions="Do the thing.",
                uses=("team/operator/delete_pod",),
            )
            for name in ("triage", "escalate")
        ]
        tree = Disclosure.of(
            Role(
                name="team",
                description="An engineering team.",
                instructions="Route.",
                children=[troubleshooter, Operator()],
            )
        )

        for skill in ("triage", "escalate"):
            card, = tree.open(f"team/troubleshooter/{skill}")["uses"]
            self.assertEqual(card["ref"], "team/operator/delete_pod")

    def test_a_reference_cycle_is_allowed_and_terminates(self) -> None:
        """`diagnose -> remediate -> diagnose` is a real workflow shape."""

        troubleshooter = _troubleshooter()
        troubleshooter.skills = [
            Skill(
                name="triage",
                description="Decide.",
                instructions="Decide.",
                uses=("team/troubleshooter/escalate",),
            ),
            Skill(
                name="escalate",
                description="Escalate.",
                instructions="Escalate.",
                uses=("team/troubleshooter/triage",),
            ),
        ]
        tree = Disclosure.of(
            Role(
                name="team",
                description="An engineering team.",
                instructions="Route.",
                children=[troubleshooter],
            )
        )

        there, = tree.open("team/troubleshooter/triage")["uses"]
        back, = tree.open("team/troubleshooter/escalate")["uses"]

        self.assertEqual(there["ref"], "team/troubleshooter/escalate")
        self.assertEqual(back["ref"], "team/troubleshooter/triage")

    def test_enumeration_does_not_follow_references(self) -> None:
        """The one place a cycle could hang the server, and it is at startup.

        `nodes_with_refs` walks containment. A cycle in the reference overlay
        is legal, so an enumerator that followed references would not return.
        """

        troubleshooter = _troubleshooter()
        troubleshooter.skills = [
            Skill(
                name="a",
                description="A.",
                instructions="A.",
                uses=("team/troubleshooter/b",),
            ),
            Skill(
                name="b",
                description="B.",
                instructions="B.",
                uses=("team/troubleshooter/a",),
            ),
        ]
        tree = Disclosure.of(
            Role(
                name="team",
                description="A team.",
                instructions="Route.",
                children=[troubleshooter],
            )
        )

        refs = [ref for ref, _ in tree.index.nodes_with_refs()]

        self.assertEqual(len(refs), len(set(refs)))
        self.assertIn("team/troubleshooter/a", refs)


class ReferenceValidationTests(unittest.TestCase):
    """A procedure whose steps do not exist must fail on the way up.

    The alternative is discovering it in front of a user, which is the whole
    reason these run beside the cycle and separator checks rather than at the
    moment somebody reaches for the capability.
    """

    def test_a_reference_to_nothing_is_refused_when_the_tree_is_built(self) -> None:
        with self.assertRaises(ModelValidationError) as caught:
            ReferenceTests._with_uses("team/operator/no_such_tool")

        message = str(caught.exception)
        self.assertIn("team/troubleshooter/triage", message)
        self.assertIn("no_such_tool", message)

    def test_naming_itself_is_refused(self) -> None:
        with self.assertRaises(ModelValidationError) as caught:
            ReferenceTests._with_uses("team/troubleshooter/triage")

        self.assertIn("itself", str(caught.exception))

    def test_a_reference_may_name_a_role(self) -> None:
        """A dependency may cross to a responsibility, not only a capability."""

        tree = ReferenceTests._with_uses("team/operator")

        card, = tree.open("team/troubleshooter/triage")["uses"]
        self.assertEqual(card["kind"], "role")
        self.assertEqual(card["ref"], "team/operator")

    def test_a_reference_may_name_an_ancestor(self) -> None:
        """Containment and dependency answer different questions."""

        tree = ReferenceTests._with_uses("team/troubleshooter")

        card, = tree.open("team/troubleshooter/triage")["uses"]
        self.assertEqual(card["ref"], "team/troubleshooter")

    def test_the_same_reference_twice_is_refused(self) -> None:
        """Two cards for one capability say there are two capabilities."""

        with self.assertRaises(ModelValidationError):
            Skill(
                name="triage",
                description="Decide.",
                instructions="Decide.",
                uses=("a/b", "a/b"),
            )

    def test_a_crossing_is_allowed_and_reported(self) -> None:
        """A person composing two branches is authorised; silence is not.

        ADR 004 governs a model guessing, not a person composing. But a
        boundary crossed silently is one nobody reviews, so the tree can list
        them.
        """

        troubleshooter = _troubleshooter()
        troubleshooter.skills = [
            Skill(
                name="triage",
                description="Decide.",
                instructions="Decide.",
                uses=("platform/operator/delete_pod",),
            )
        ]
        tree = Disclosure.of(
            [
                Role(
                    name="team",
                    description="A team.",
                    instructions="Route.",
                    children=[troubleshooter],
                ),
                Role(
                    name="platform",
                    description="The platform.",
                    instructions="Route.",
                    children=[Operator()],
                ),
            ]
        )

        self.assertEqual(
            list(tree.index.crossings()),
            [("team/troubleshooter/triage", "platform/operator/delete_pod", "platform")],
        )

    def test_a_reference_inside_one_root_is_not_a_crossing(self) -> None:
        self.assertEqual(
            list(ReferenceTests._with_uses("team/operator/delete_pod").index.crossings()),
            [],
        )
