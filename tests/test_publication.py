"""Publication declarations, pure disclosure, and selected containment trees."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from enum import Enum
import json
import unittest

from contexture import Channels, Contexture, Publication, Role, Skill, Tool, inspection
from contexture.core.constants import OPEN_TOOL
from contexture.core.errors import DuplicateNameError, ModelValidationError
from contexture.core.model.disclosure import Disclosure
from contexture.core.model.index import Index
from contexture.core.model.manager import ControllerManager
from contexture.core.model.root_selection import (
    RootOutsideSelectionError,
    SurfaceSelection,
)
from contexture.server import compile_application, compile_disclosure_application


OWNER_TEXT = "  Prepare inputs, then do the owner's work.\n "
PROCEDURE = "Consolidate PRIVATE findings and request approval before preservation."
METHOD = "Check PRIVATE acceptance evidence against the original task."
CONTRACT = "Publication (framework contract):"
PUB_REF = "worker/preserve-results"


def role(name: str = "worker", **kwargs) -> Role:
    return Role(
        name=name,
        description=f"The {name} responsibility.",
        instructions=OWNER_TEXT,
        **kwargs,
    )


class Preserve(Tool):
    def __init__(self, name: str = "preserve") -> None:
        super().__init__(name=name, description="Preserve approved results.")
        self.calls = 0

    async def invoke(self, approved_evidence: str) -> str:
        self.calls += 1
        return approved_evidence


class WorkTool(Preserve):
    async def invoke(self, task_input: str) -> str:
        self.calls += 1
        return task_input


class Evidence(Skill):
    def __init__(self, name: str = "evidence") -> None:
        super().__init__(
            name=name, description="Review acceptance evidence.", instructions=METHOD
        )


class Results(Publication):
    def __init__(
        self,
        *,
        name: str = "preserve-results",
        publication: Publication | None = None,
        uses: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            name=name,
            description="Make completed work reusable.",
            instructions=PROCEDURE,
            children=[role("review")],
            publication=publication,
            skills=[Evidence()],
            tools=[Preserve()],
            uses=uses,
        )


class Worker(Role):
    def __init__(self) -> None:
        super().__init__(
            name="worker",
            description="Complete the assigned work.",
            instructions=OWNER_TEXT,
            children=[role("child")],
            publication=Results(),
            skills=[Evidence("owner-method")],
            tools=[WorkTool("owner-tool")],
        )


class _DefaultPublication(Enum):
    OMITTED = "omitted"


class BusinessBase(Role):
    def __init__(self, *, publication: Publication | None = None) -> None:
        super().__init__(
            name="business",
            description="Perform business work.",
            instructions=OWNER_TEXT,
            publication=publication,
        )


class PublishingBusiness(BusinessBase):
    def __init__(
        self,
        *,
        publication: Publication | None | _DefaultPublication = (
            _DefaultPublication.OMITTED
        ),
    ) -> None:
        chosen = Results() if publication is _DefaultPublication.OMITTED else publication
        super().__init__(publication=chosen)


class PublicationModelTests(unittest.TestCase):
    def test_inherited_keyword_constructor_and_member_order(self) -> None:
        child, skill, tool, nested = role("child"), Evidence(), Preserve(), Results()
        publication = Publication(
            name="publish",
            description="Keep useful results.",
            instructions=PROCEDURE,
            children=[child],
            publication=nested,
            skills=[skill],
            tools=[tool],
            uses=("shared/preserve",),
        )
        self.assertIsInstance(publication, Role)
        self.assertEqual((publication.kind, publication.group), ("role", "roles"))
        self.assertEqual(list(publication.members()), [child, nested, skill, tool])
        self.assertEqual(publication.branches(), (child,))
        self.assertIs(publication.member(nested.name), nested)
        self.assertEqual(publication.uses, ("shared/preserve",))
        with self.assertRaises(TypeError):
            Publication("publish", "Keep results.", PROCEDURE)

    def test_publication_requires_a_constructed_specialized_role(self) -> None:
        for invalid in (Results, Publication, role(), Evidence(), Preserve(), object(), 1):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaisesRegex(ModelValidationError, "constructed Publication"):
                    role(publication=invalid)

    def test_publication_uses_normal_required_metadata_validation(self) -> None:
        for field in ("name", "description", "instructions"):
            with self.subTest(field=field):
                values = dict(name="publish", description="Preserve.", instructions=PROCEDURE)
                values[field] = " \n "
                with self.assertRaises(ModelValidationError):
                    Publication(**values)

    def test_publication_shares_the_cross_kind_member_namespace(self) -> None:
        for field, sibling in (
            ("children", role("preserve-results")),
            ("skills", Evidence("preserve-results")),
            ("tools", Preserve("preserve-results")),
        ):
            with self.subTest(field=field):
                with self.assertRaises(DuplicateNameError):
                    role(publication=Results(), **{field: [sibling]})

    def test_shared_publication_or_its_equipment_is_rejected(self) -> None:
        shared = Results()
        with self.assertRaises(ModelValidationError):
            Index.of([role("left", publication=shared), role("right", publication=shared)])

        shared_tool = Preserve()
        left = Publication(name="left", description="Left.", instructions=PROCEDURE,
                           tools=[shared_tool])
        right = Publication(name="right", description="Right.", instructions=PROCEDURE,
                            tools=[shared_tool])
        with self.assertRaises(ModelValidationError):
            Index.of(role(publication=left, children=[right]))

    def test_containment_cycles_through_publications_are_rejected(self) -> None:
        owner = role(publication=Results())
        owner.publication.children.append(owner)
        with self.assertRaisesRegex(ModelValidationError, "contains itself"):
            Index.of(owner)

        outer, inner = Results(), Results(name="nested")
        outer.publication = inner
        inner.publication = outer
        with self.assertRaisesRegex(ModelValidationError, "contains itself"):
            Index.of(role(publication=outer))

    def test_separator_validation_reaches_publication_and_nested_members(self) -> None:
        for bad in (
            Results(name="bad/name"),
            Publication(name="publish", description="Preserve.", instructions=PROCEDURE,
                        tools=[Preserve("bad/tool")]),
        ):
            with self.subTest(name=bad.name):
                with self.assertRaisesRegex(ModelValidationError, "separates"):
                    Index.of(role(publication=bad))

    def test_business_inheritance_distinguishes_default_override_and_none(self) -> None:
        class SpecializedBusiness(PublishingBusiness):
            pass

        base, first, second = BusinessBase(), SpecializedBusiness(), SpecializedBusiness()
        replacement = Results(name="custom-publication")
        overridden = SpecializedBusiness(publication=replacement)
        disabled = SpecializedBusiness(publication=None)
        self.assertIsNone(base.publication)
        self.assertIsNone(disabled.publication)
        self.assertIs(overridden.publication, replacement)
        self.assertIsInstance(first.publication, Results)
        self.assertIsNot(first.publication, second.publication)
        self.assertIsNot(first.publication.tools[0], second.publication.tools[0])
        self.assertEqual(base.compile("active"), disabled.compile("active"))
        self.assertEqual(
            Disclosure.of(overridden).open("business")["publication"],
            "business/custom-publication",
        )
        self.assertEqual(first.instructions, OWNER_TEXT)

    def test_nested_publication_is_explicit_and_containment_does_not_inherit(self) -> None:
        tree = Disclosure.of(role(publication=Results(publication=Results(name="nested")),
                                  children=[role("child")]))
        outer = tree.open(PUB_REF)
        nested = tree.open(f"{PUB_REF}/nested")
        child = tree.open("worker/child")
        review = tree.open(f"{PUB_REF}/review")
        self.assertEqual(outer["publication"], f"{PUB_REF}/nested")
        self.assertEqual(outer["instructions"].count(CONTRACT), 1)
        for opened in (nested, child, review):
            self.assertNotIn("publication", opened)
            self.assertNotIn(CONTRACT, opened["instructions"])
        self.assertEqual(nested["instructions"], PROCEDURE)
        self.assertIsNone(tree.index.find("worker/child").publication)
        self.assertIsNone(tree.index.find(f"{PUB_REF}/nested").publication)


class PublicationDisclosureTests(unittest.TestCase):
    def test_none_preserves_exact_legacy_payload_and_instruction_whitespace(self) -> None:
        expected_route = {
            "kind": "role", "name": "worker", "description": "The worker responsibility."
        }
        expected_active = {
            **expected_route, "ref": "worker", "instructions": OWNER_TEXT,
            "roles": [], "skills": [], "tools": [],
        }
        for declaration in ({}, {"publication": None}):
            with self.subTest(declaration=declaration):
                owner = role(**declaration)
                self.assertEqual(list(owner.members()), [])
                self.assertEqual(owner.compile("route"), expected_route)
                self.assertEqual(Disclosure.of(owner).open("worker"), expected_active)
                self.assertEqual(owner.instructions, OWNER_TEXT)

    def test_route_and_discovery_do_not_disclose_publication(self) -> None:
        owner = Worker()
        tree = Disclosure.of(owner)
        self.assertEqual(owner.compile("route"), {
            "kind": "role", "name": "worker", "description": "Complete the assigned work."
        })
        self.assertEqual(tree.skeleton(), {
            "roles": [{
                "kind": "role", "name": "worker",
                "description": "Complete the assigned work.", "ref": "worker",
            }],
            "skills": [], "tools": [],
        })
        self.assertEqual(owner.publication.compile("route"), {
            "kind": "role", "name": "preserve-results",
            "description": "Make completed work reusable.",
        })

    def test_active_owner_has_one_normal_card_and_string_designation(self) -> None:
        tree = Disclosure.of(role(publication=Results(), children=[role("child")]))
        opened = tree.open("worker")
        instructions = opened["instructions"]
        self.assertEqual({key: value for key, value in opened.items() if key != "instructions"}, {
            "kind": "role", "name": "worker", "description": "The worker responsibility.",
            "ref": "worker", "publication": PUB_REF,
            "roles": [
                {"kind": "role", "name": "child", "description": "The child responsibility.",
                 "ref": "worker/child"},
                {"kind": "role", "name": "preserve-results",
                 "description": "Make completed work reusable.", "ref": PUB_REF},
            ],
            "skills": [], "tools": [],
        })
        self.assertIsInstance(opened["publication"], str)
        self.assertTrue(instructions.startswith(f"{OWNER_TEXT}\n\n{CONTRACT}\n"))
        for fragment in (
            OPEN_TOOL, repr(PUB_REF), "Before finishing", "results and evidence",
            "does not execute", "establish success", "approvals", "blocked",
            "fails", "awaits approval", "rather than claiming success", "bypassing approval",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, instructions)

    def test_publication_procedure_and_equipment_wait_until_opened(self) -> None:
        tree = compile_application(Contexture(name="publication", roots=(Worker,))).disclosure
        owner = tree.open("worker")
        owner_text = json.dumps(owner)
        for hidden in (PROCEDURE, METHOD, "approved_evidence", f"{PUB_REF}/preserve"):
            self.assertNotIn(hidden, owner_text)
        self.assertEqual([card["name"] for card in owner["skills"]], ["owner-method"])
        self.assertEqual([card["name"] for card in owner["tools"]], ["owner-tool"])
        publication = tree.open(PUB_REF)
        self.assertEqual(publication["instructions"], PROCEDURE)
        self.assertNotIn("publication", publication)
        self.assertEqual([card["ref"] for card in publication["skills"]], [f"{PUB_REF}/evidence"])
        self.assertNotIn(METHOD, json.dumps(publication))
        tool_card = publication["tools"][0]
        self.assertEqual(tool_card["ref"], f"{PUB_REF}/preserve")
        self.assertIn("approved_evidence", tool_card["input_schema"]["properties"])
        self.assertEqual(tree.open(f"{PUB_REF}/evidence")["instructions"], METHOD)

    def test_repeated_open_does_not_mutate_business_text_or_reuse_payloads(self) -> None:
        owner = Worker()
        tree = Disclosure.of(owner)
        expected = tree.open("worker")
        changed = tree.open("worker")
        changed["instructions"] = "caller mutation"
        changed["roles"].clear()
        for _ in range(3):
            tree.open(PUB_REF)
            self.assertEqual(tree.open("worker"), expected)
            self.assertEqual(tree.open("worker")["instructions"].count(CONTRACT), 1)
        self.assertEqual(owner.instructions, OWNER_TEXT)
        self.assertEqual(owner.publication.instructions, PROCEDURE)

    def test_instruction_ref_comes_from_the_view_not_a_guessed_path(self) -> None:
        class AliasedDisclosure(Disclosure):
            def ref_of(self, node):
                return f"surface:{super().ref_of(node)}"

        tree = AliasedDisclosure(Index.of(Worker))
        opened = tree.open("worker")
        actual_ref = f"surface:{PUB_REF}"
        self.assertEqual(opened["publication"], actual_ref)
        self.assertEqual(opened["roles"][1]["ref"], actual_ref)
        self.assertIn(f"ref={actual_ref!r}", opened["instructions"])
        self.assertNotIn(f"ref={PUB_REF!r}", opened["instructions"])

    def test_custom_cards_policy_cannot_silently_hide_the_publication(self) -> None:
        class WithoutPublication(Disclosure):
            def cards_of(self, nodes):
                return super().cards_of(node for node in nodes if not isinstance(node, Publication))

        tree = WithoutPublication(Index.of(role(publication=Results(name="private-finalizer"))))
        with self.assertRaisesRegex(ModelValidationError, "unavailable") as caught:
            tree.open("worker")
        self.assertNotIn("private-finalizer", str(caught.exception))
        self.assertNotIn("worker", str(caught.exception))

    def test_direct_open_of_prompt_only_owner_fails_without_hidden_path(self) -> None:
        tree = Disclosure(Index.of(Worker), prompt_roots=frozenset({"worker"}))
        self.assertEqual(tree.skeleton(), {"roles": [], "skills": [], "tools": []})
        with self.assertRaisesRegex(ModelValidationError, "unavailable") as caught:
            tree.open("worker")
        for hidden in ("worker", "preserve-results", PUB_REF, OWNER_TEXT, PROCEDURE):
            self.assertNotIn(hidden, str(caught.exception))
        self.assertEqual(tree.unrestricted().open("worker")["publication"], PUB_REF)


class PublicationTraversalTests(unittest.TestCase):
    def test_full_containment_walk_is_distinct_from_alternative_branch_axis(self) -> None:
        index = Index.of(Worker)
        refs = [
            "worker", "worker/child", PUB_REF, f"{PUB_REF}/review",
            f"{PUB_REF}/evidence", f"{PUB_REF}/preserve",
            "worker/owner-method", "worker/owner-tool",
        ]
        self.assertEqual([ref for ref, _ in index.walk()], refs)
        self.assertEqual([ref for ref, _ in index.roles_by_level()], ["worker", "worker/child"])
        self.assertEqual([ref for ref, _ in index.roles_with_refs()], refs[:4])
        self.assertEqual([node.name for node in index.of_kind("role")],
                         ["worker", "child", "preserve-results", "review"])
        self.assertEqual(index.signpost(f"{PUB_REF}/preserve"),
                         (("worker", 1), (PUB_REF, 1)))
        owner, publication = index.find("worker"), index.find(PUB_REF)
        self.assertEqual(index.children_of(owner), tuple(owner.members()))
        self.assertIs(index.parent_of(publication), owner)
        self.assertIs(index.parent_of(index.find(f"{PUB_REF}/preserve")), publication)
        self.assertEqual(publication.path, ("worker", "preserve-results"))

    def test_inspection_includes_nested_publications_and_never_executes_tools(self) -> None:
        tree = Disclosure.of(role(publication=Results(publication=Results(name="nested"))))
        expected_refs = [ref for ref, _ in tree.index.walk()]
        self.assertCountEqual(list(inspection.every_ref(tree)), expected_refs)
        self.assertIn(f"{PUB_REF}/nested/preserve", expected_refs)
        for ref in expected_refs:
            with self.subTest(ref=ref):
                step = inspection.open_step(tree, ref)
                self.assertFalse(step.refused)
                self.assertEqual(step.payload, tree.open(ref))
                self.assertEqual(json.loads(step.body), tree.open(ref))
        self.assertTrue(all(tool.calls == 0 for tool in tree.index.of_kind("tool")))

    def test_uses_are_normal_routing_cards_with_dependents_and_crossings(self) -> None:
        tree = Disclosure.of([
            role(publication=Results(uses=("shared/preserve",))),
            role("shared", tools=[Preserve()]),
        ])
        opened = tree.open(PUB_REF)
        self.assertEqual(opened["uses"], [tree.card_for("shared/preserve")])
        self.assertEqual(tree.index.uses_of(PUB_REF), ("shared/preserve",))
        self.assertEqual(tree.index.dependents_of("shared/preserve"), (PUB_REF,))
        self.assertEqual(list(tree.index.crossings()), [(PUB_REF, "shared/preserve", "shared")])
        self.assertNotIn("uses", opened["uses"][0])
        self.assertNotIn("shared/preserve", [ref for ref, _ in tree.index.walk()
                                           if ref.startswith(f"{PUB_REF}/")])

    def test_unresolved_self_and_duplicate_uses_are_rejected(self) -> None:
        for refs, message in (
            (("missing/preserve",), "resolves to nothing"),
            ((PUB_REF,), "itself"),
            (("shared/preserve", "shared/preserve"), "same reference twice"),
        ):
            with self.subTest(refs=refs):
                with self.assertRaisesRegex(ModelValidationError, message):
                    Index.of(role(publication=Results(uses=refs)))

    def test_reference_cycles_are_cards_not_containment_cycles(self) -> None:
        tree = Disclosure.of(role(publication=Results(uses=("worker",)), uses=(PUB_REF,)))
        self.assertEqual(tree.open("worker")["uses"], [tree.card_for(PUB_REF)])
        self.assertEqual(tree.open(PUB_REF)["uses"], [tree.card_for("worker")])
        self.assertNotIn("publication", tree.open(PUB_REF)["uses"][0])
        self.assertEqual(tree.index.dependents_of(PUB_REF), ("worker",))


class PublicationSelectionAndCompilationTests(unittest.TestCase):
    def test_selecting_owner_includes_the_complete_publication_subtree(self) -> None:
        tree = Disclosure.of([Worker(), role("other")])
        selected = tree.select(SurfaceSelection.only("worker"))
        self.assertEqual([card["ref"] for card in selected.skeleton()["roles"]], ["worker"])
        self.assertEqual(selected.open("worker")["publication"], PUB_REF)
        self.assertEqual(selected.open(PUB_REF)["instructions"], PROCEDURE)
        self.assertEqual(selected.open(f"{PUB_REF}/evidence")["instructions"], METHOD)
        self.assertEqual(selected.open(f"{PUB_REF}/preserve")["kind"], "tool")
        self.assertEqual(
            [ref for ref, _ in selected.selected_graph().walk()],
            [ref for ref, _ in tree.index.walk() if ref != "other"],
        )
        with self.assertRaises(RootOutsideSelectionError):
            selected.open("other")

    def test_selecting_publication_promotes_it_without_owner_or_siblings(self) -> None:
        selected = Disclosure.of(Worker).select(SurfaceSelection.only(PUB_REF))
        self.assertEqual(selected.skeleton(), {
            "roles": [{
                "kind": "role", "name": "preserve-results",
                "description": "Make completed work reusable.", "ref": PUB_REF,
            }],
            "skills": [], "tools": [],
        })
        opened = selected.open(PUB_REF)
        self.assertEqual(opened["instructions"], PROCEDURE)
        self.assertNotIn("publication", opened)
        self.assertNotIn(OWNER_TEXT, json.dumps(opened))
        graph = selected.selected_graph()
        self.assertIsNone(graph.parent_of(graph.find(PUB_REF)))
        self.assertEqual([ref for ref, _ in graph.walk()],
                         [PUB_REF, f"{PUB_REF}/review", f"{PUB_REF}/evidence", f"{PUB_REF}/preserve"])
        for hidden in ("worker", "worker/child", "worker/owner-method", "worker/owner-tool"):
            with self.subTest(hidden=hidden):
                with self.assertRaises(RootOutsideSelectionError):
                    selected.open(hidden)

    def test_uses_never_widen_selection_or_reveal_prompt_only_targets(self) -> None:
        index = Index.of([
            role(publication=Results(uses=("shared/preserve", "private/preserve"))),
            role("shared", tools=[Preserve()]),
            role("private", tools=[Preserve()]),
        ])
        tree = Disclosure(index, prompt_roots=frozenset({"private"}))
        self.assertEqual(tree.open(PUB_REF)["uses"], [tree.card_for("shared/preserve")])
        selected = tree.select(SurfaceSelection.only(PUB_REF))
        self.assertEqual(selected.open(PUB_REF)["uses"], [])
        self.assertNotIn("private", json.dumps(selected.open(PUB_REF)))
        self.assertEqual(selected.selected_graph().uses_of(PUB_REF), ())
        with self.assertRaises(RootOutsideSelectionError):
            selected.open("shared/preserve")
        widened = tree.select(SurfaceSelection.only([PUB_REF, "shared/preserve"]))
        self.assertEqual(widened.open(PUB_REF)["uses"], [tree.card_for("shared/preserve")])
        self.assertFalse(widened.model_can_see("private/preserve"))

    def test_lazy_class_declaration_builds_fresh_nodes_and_stamps_shared_channels(self) -> None:
        built = []

        class CountedWorker(Worker):
            def __init__(self) -> None:
                super().__init__()
                built.append(self)

        app = Contexture(name="publication", roots=(CountedWorker,), channels=Channels)
        self.assertEqual(built, [])
        first, second = compile_application(app), compile_application(app)
        self.assertEqual(len(built), 2)
        self.assertIsNot(first.index.channels, second.index.channels)
        self.assertEqual(first.disclosure.open("worker"), second.disclosure.open("worker"))
        for ref, node in first.index.walk():
            with self.subTest(ref=ref):
                self.assertIsNot(node, second.index.find(ref))
                self.assertIs(node.channels, first.index.channels)
                self.assertIs(second.index.find(ref).channels, second.index.channels)
                self.assertEqual(node.path, tuple(ref.split("/")))
        manager = ControllerManager(channels=Channels())
        manager.register_role(Worker)
        replacement = Channels()
        manager.rebind_channels(replacement)
        self.assertTrue(all(node.channels is replacement for _, node in Index.of(manager).walk()))

    def test_disclosure_only_compile_keeps_publication_equipment_unbound(self) -> None:
        compiled = compile_disclosure_application(Contexture(name="publication", roots=(Worker,)))
        self.assertFalse(compiled.index.is_bound)
        self.assertEqual(compiled.disclosure.open("worker")["publication"], PUB_REF)
        opened = compiled.disclosure.open(PUB_REF)
        self.assertEqual(opened["instructions"], PROCEDURE)
        tool = compiled.disclosure.open(f"{PUB_REF}/preserve")
        self.assertEqual(tool, {
            "kind": "tool", "name": "preserve", "description": "Preserve approved results.",
            "ref": f"{PUB_REF}/preserve",
        })
        self.assertEqual(opened["tools"], [tool])
        with self.assertRaisesRegex(ModelValidationError, "no executable bindings"):
            compiled.index.binding_of(f"{PUB_REF}/preserve")
        self.assertEqual(compiled.index.find(f"{PUB_REF}/preserve").calls, 0)

    def test_concurrent_compiles_and_opens_are_idempotent_and_isolated(self) -> None:
        app = Contexture(name="publication", roots=(Worker,))

        def compile_and_open(_):
            compiled = compile_disclosure_application(app)
            tree = compiled.disclosure
            for ref in inspection.every_ref(tree):
                inspection.open_step(tree, ref)
            return compiled, [tree.open("worker") for _ in range(3)]

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(compile_and_open, range(4)))
        expected = results[0][1][0]
        seen = set()
        for compiled, payloads in results:
            self.assertEqual(payloads, [expected] * 3)
            self.assertEqual(payloads[0]["instructions"].count(CONTRACT), 1)
            for _, node in compiled.index.walk():
                self.assertNotIn(id(node), seen)
                seen.add(id(node))
            self.assertEqual(compiled.index.find("worker").instructions, OWNER_TEXT)
            self.assertEqual(compiled.index.find(f"{PUB_REF}/preserve").calls, 0)
        results[0][1][0]["roles"].clear()
        self.assertTrue(results[0][1][1]["roles"])
        self.assertTrue(results[1][1][0]["roles"])
