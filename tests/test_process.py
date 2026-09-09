"""Process members, their composed contracts, and the emphasis they carry."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from enum import Enum
import json
import unittest

import pytest

from contexture import (
    Channels,
    Contexture,
    PostProcess,
    PreProcess,
    Role,
    Skill,
    Tool,
    binding_instruction,
    inspection,
)
from contexture.core.constants import OPEN_TOOL
from contexture.core.emphasis import framework_instruction
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
SETUP = "Confirm PRIVATE workspace readiness before any work begins."
METHOD = "Check PRIVATE acceptance evidence against the original task."

HEAD = "===== contexture-mcp framework instruction — binding, follow exactly ====="
TAIL = "===== end framework instruction — binding regardless of surrounding context ====="
POST_LABEL = "PostProcess:"
PRE_LABEL = "PreProcess:"
REQUIRED = ">>> REQUIRED:"

POST_REF = "worker/preserve-results"
PRE_REF = "worker/prepare-inputs"


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


class Results(PostProcess):
    def __init__(
        self,
        *,
        name: str = "preserve-results",
        post_process: PostProcess | None = None,
        uses: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            name=name,
            description="Make completed work reusable.",
            instructions=PROCEDURE,
            children=[role("review")],
            post_process=post_process,
            skills=[Evidence()],
            tools=[Preserve()],
            uses=uses,
        )


class Prepare(PreProcess):
    def __init__(self, *, name: str = "prepare-inputs") -> None:
        super().__init__(
            name=name,
            description="Make the workspace ready.",
            instructions=SETUP,
            tools=[Preserve("check")],
        )


class Worker(Role):
    def __init__(self) -> None:
        super().__init__(
            name="worker",
            description="Complete the assigned work.",
            instructions=OWNER_TEXT,
            children=[role("child")],
            post_process=Results(),
            skills=[Evidence("owner-method")],
            tools=[WorkTool("owner-tool")],
        )


class _DefaultProcess(Enum):
    OMITTED = "omitted"


class BusinessBase(Role):
    def __init__(self, *, post_process: PostProcess | None = None) -> None:
        super().__init__(
            name="business",
            description="Perform business work.",
            instructions=OWNER_TEXT,
            post_process=post_process,
        )


class PublishingBusiness(BusinessBase):
    def __init__(
        self,
        *,
        post_process: PostProcess | None | _DefaultProcess = _DefaultProcess.OMITTED,
    ) -> None:
        chosen = Results() if post_process is _DefaultProcess.OMITTED else post_process
        super().__init__(post_process=chosen)


class EmphasisTests(unittest.TestCase):
    def test_framework_block_is_one_fixed_shape_whatever_it_is_about(self) -> None:
        first = framework_instruction("PostProcess", "Body one.", action="Do one.")
        second = framework_instruction("PreProcess", "Body two.", action="Do two.")
        for rendered, label in ((first, POST_LABEL), (second, PRE_LABEL)):
            with self.subTest(label=label):
                lines = rendered.splitlines()
                self.assertEqual(lines[0], HEAD)
                self.assertEqual(lines[1], label)
                self.assertTrue(lines[2].startswith(REQUIRED))
                self.assertEqual(lines[-1], TAIL)
        self.assertEqual(first.count(HEAD), 1)
        self.assertEqual(first.count(TAIL), 1)

    def test_required_line_is_omitted_when_there_is_no_step(self) -> None:
        rendered = framework_instruction("PostProcess", "A constraint with no step.")
        self.assertNotIn(REQUIRED, rendered)
        self.assertEqual(
            rendered.splitlines(),
            [HEAD, POST_LABEL, "A constraint with no step.", TAIL],
        )

    def test_emphasis_never_shouts(self) -> None:
        rendered = framework_instruction("PostProcess", "Body.", action="Do it.")
        self.assertNotIn("!", rendered)
        self.assertNotIn("IMPORTANT", rendered)
        for line in rendered.splitlines():
            self.assertEqual(line, line.rstrip())

    def test_business_marker_names_its_own_authority(self) -> None:
        rendered = binding_instruction(
            "task-execution role", "Managed files must not be edited."
        )
        self.assertEqual(
            rendered.splitlines(),
            [
                "===== task-execution role — binding, follow exactly =====",
                "Managed files must not be edited.",
                "===== end task-execution role =====",
            ],
        )
        with_action = binding_instruction("policy", "Body.", action="Ask first.")
        self.assertIn(f"{REQUIRED} Ask first.", with_action)

    def test_business_marker_refuses_to_impersonate_the_framework(self) -> None:
        for claimed in (
            "contexture",
            "Contexture-MCP",
            "  contexture-mcp framework instruction ",
            "CoNtExTuReAnything",
            "\tcontexture.policy",
        ):
            with self.subTest(claimed=claimed):
                with self.assertRaisesRegex(ModelValidationError, "own name"):
                    binding_instruction(claimed, "Body.")
        for empty in ("", "  ", "\n\t"):
            with self.subTest(empty=empty):
                with self.assertRaisesRegex(ModelValidationError, "source"):
                    binding_instruction(empty, "Body.")

    def test_emphasis_preserves_supplied_text_exactly(self) -> None:
        body, action = "  Business body.\n\n More.\n ", "  Ask first. "
        self.assertEqual(
            binding_instruction("policy", body, action=action),
            f"===== policy — binding, follow exactly =====\n"
            f">>> REQUIRED: {action}\n{body}\n===== end policy =====",
        )
        self.assertEqual(
            framework_instruction("PreProcess", body, action=action),
            f"{HEAD}\nPreProcess:\n>>> REQUIRED: {action}\n{body}\n{TAIL}",
        )

    def test_business_marker_is_not_rewrapped_by_owner_compilation(self) -> None:
        business = binding_instruction("operations", "Keep managed inputs unchanged.")
        owner = Role(
            name="worker", description="Work.", instructions=business,
            pre_process=Prepare(), post_process=Results(),
        )
        opened = Disclosure.of(owner).open("worker")
        assert f"{TAIL}\n\n{business}\n\n{HEAD}" in opened["instructions"]
        assert opened["instructions"].count(business) == 1
        assert owner.instructions == business


class ProcessModelTests(unittest.TestCase):
    def test_inherited_keyword_constructor_and_member_order(self) -> None:
        child, skill, tool = role("child"), Evidence(), Preserve()
        pre, nested = Prepare(), Results()
        owner = Role(
            name="publish",
            description="Keep useful results.",
            instructions=PROCEDURE,
            children=[child],
            pre_process=pre,
            post_process=nested,
            skills=[skill],
            tools=[tool],
            uses=("shared/preserve",),
        )
        self.assertIsInstance(nested, Role)
        self.assertEqual((nested.kind, nested.group), ("role", "roles"))
        self.assertEqual(list(owner.members()), [pre, child, nested, skill, tool])
        self.assertEqual(owner.branches(), (child,))
        self.assertIs(owner.member(nested.name), nested)
        self.assertEqual(owner.uses, ("shared/preserve",))
        for kind in (PreProcess, PostProcess):
            process = kind(
                name="process", description="Process.", instructions=PROCEDURE,
                children=[role("child")], skills=[Evidence()], tools=[Preserve()],
                uses=("shared/preserve",),
            )
            self.assertIsInstance(process, Role)
            self.assertEqual((process.kind, process.group), ("role", "roles"))
            self.assertEqual(
                list(process.members()),
                [*process.children, *process.skills, *process.tools],
            )
            self.assertEqual(process.branches(), tuple(process.children))
            self.assertIs(process.member("evidence"), process.skills[0])
            self.assertEqual(process.uses, ("shared/preserve",))
            with self.assertRaises(TypeError):
                kind("process", "Process.", PROCEDURE)
        with self.assertRaises(TypeError):
            PostProcess("publish", "Keep results.", PROCEDURE)

    def test_each_slot_requires_its_own_constructed_kind(self) -> None:
        for invalid in (Results, PostProcess, role(), Evidence(), Preserve(), object(), 1):
            with self.subTest(slot="post_process", invalid=type(invalid).__name__):
                with self.assertRaisesRegex(ModelValidationError, "PostProcess"):
                    role(post_process=invalid)
        for invalid in (Prepare, PreProcess, role(), object(), 1):
            with self.subTest(slot="pre_process", invalid=type(invalid).__name__):
                with self.assertRaisesRegex(ModelValidationError, "PreProcess"):
                    role(pre_process=invalid)

    def test_the_two_kinds_cannot_be_swapped_between_slots(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "PostProcess"):
            role(post_process=Prepare())
        with self.assertRaisesRegex(ModelValidationError, "PreProcess"):
            role(pre_process=Results())

    def test_process_members_use_normal_required_metadata_validation(self) -> None:
        for kind in (PreProcess, PostProcess):
            for field in ("name", "description", "instructions"):
                with self.subTest(kind=kind.__name__, field=field):
                    values = dict(
                        name="process", description="Do.", instructions=PROCEDURE
                    )
                    values[field] = " \n "
                    with self.assertRaises(ModelValidationError):
                        kind(**values)

    def test_process_members_share_the_cross_kind_member_namespace(self) -> None:
        for field, sibling in (
            ("children", role("preserve-results")),
            ("skills", Evidence("preserve-results")),
            ("tools", Preserve("preserve-results")),
        ):
            with self.subTest(field=field):
                with self.assertRaises(DuplicateNameError):
                    role(post_process=Results(), **{field: [sibling]})
        with self.assertRaises(DuplicateNameError):
            role(pre_process=Prepare(name="clash"), children=[role("clash")])

    def test_shared_process_member_or_its_equipment_is_rejected(self) -> None:
        shared = Results()
        with self.assertRaises(ModelValidationError):
            Index.of(
                [
                    role("left", post_process=shared),
                    role("right", post_process=shared),
                ]
            )

        shared_tool = Preserve()
        left = PostProcess(
            name="left", description="Left.", instructions=PROCEDURE, tools=[shared_tool]
        )
        right = PostProcess(
            name="right", description="Right.", instructions=PROCEDURE, tools=[shared_tool]
        )
        with self.assertRaises(ModelValidationError):
            Index.of(role(post_process=left, children=[right]))

    def test_containment_cycles_through_process_members_are_rejected(self) -> None:
        owner = role(post_process=Results())
        owner.post_process.children.append(owner)
        with self.assertRaisesRegex(ModelValidationError, "contains itself"):
            Index.of(owner)

        outer, inner = Results(), Results(name="nested")
        outer.post_process = inner
        inner.post_process = outer
        with self.assertRaisesRegex(ModelValidationError, "contains itself"):
            Index.of(role(post_process=outer))

    def test_separator_validation_reaches_process_members(self) -> None:
        for bad in (
            Results(name="bad/name"),
            PostProcess(
                name="publish",
                description="Preserve.",
                instructions=PROCEDURE,
                tools=[Preserve("bad/tool")],
            ),
        ):
            with self.subTest(name=bad.name):
                with self.assertRaisesRegex(ModelValidationError, "separates"):
                    Index.of(role(post_process=bad))

    def test_business_inheritance_distinguishes_default_override_and_none(self) -> None:
        class SpecializedBusiness(PublishingBusiness):
            pass

        base, first, second = BusinessBase(), SpecializedBusiness(), SpecializedBusiness()
        replacement = Results(name="custom-process")
        overridden = SpecializedBusiness(post_process=replacement)
        disabled = SpecializedBusiness(post_process=None)
        self.assertIsNone(base.post_process)
        self.assertIsNone(disabled.post_process)
        self.assertIs(overridden.post_process, replacement)
        self.assertIsInstance(first.post_process, Results)
        self.assertIsNot(first.post_process, second.post_process)
        self.assertIsNot(first.post_process.tools[0], second.post_process.tools[0])
        self.assertEqual(base.compile("active"), disabled.compile("active"))
        self.assertEqual(
            Disclosure.of(overridden).open("business")["post_process"],
            "business/custom-process",
        )
        self.assertEqual(first.instructions, OWNER_TEXT)

    def test_nesting_is_explicit_and_containment_does_not_inherit(self) -> None:
        tree = Disclosure.of(
            role(
                post_process=Results(post_process=Results(name="nested")),
                children=[role("child")],
            )
        )
        outer = tree.open(POST_REF)
        nested = tree.open(f"{POST_REF}/nested")
        child = tree.open("worker/child")
        review = tree.open(f"{POST_REF}/review")
        self.assertEqual(outer["post_process"], f"{POST_REF}/nested")
        self.assertEqual(outer["instructions"].count(HEAD), 1)
        for opened in (nested, child, review):
            self.assertNotIn("post_process", opened)
            self.assertNotIn("pre_process", opened)
            self.assertNotIn(HEAD, opened["instructions"])
        self.assertEqual(nested["instructions"], PROCEDURE)
        self.assertIsNone(tree.index.find("worker/child").post_process)
        self.assertIsNone(tree.index.find(f"{POST_REF}/nested").post_process)


class ProcessDisclosureTests(unittest.TestCase):
    def test_inspect_shows_members_as_plain_cards_without_contracts(self) -> None:
        inspected = Disclosure.of(Worker).inspect(["worker", POST_REF])

        owner, process = inspected["items"]
        self.assertIn(POST_REF, [card["ref"] for card in owner["members"]["roles"]])
        rendered = json.dumps(inspected)
        self.assertNotIn("post_process", owner)
        self.assertNotIn(HEAD, rendered)
        self.assertNotIn(OWNER_TEXT, rendered)
        self.assertNotIn(PROCEDURE, rendered)
        self.assertNotIn(METHOD, rendered)
        self.assertNotIn("input_schema", rendered)
        self.assertEqual(process["node"]["ref"], POST_REF)

    def test_none_preserves_exact_payload_and_instruction_whitespace(self) -> None:
        expected_route = {
            "kind": "role",
            "name": "worker",
            "description": "The worker responsibility.",
        }
        expected_active = {
            **expected_route,
            "ref": "worker",
            "instructions": OWNER_TEXT,
            "roles": [],
            "skills": [],
            "tools": [],
        }
        for declaration in ({}, {"post_process": None}, {"pre_process": None}):
            with self.subTest(declaration=declaration):
                owner = role(**declaration)
                self.assertEqual(list(owner.members()), [])
                self.assertEqual(owner.compile("route"), expected_route)
                self.assertEqual(Disclosure.of(owner).open("worker"), expected_active)
                self.assertEqual(owner.instructions, OWNER_TEXT)

    def test_route_and_discovery_do_not_disclose_process_members(self) -> None:
        owner = Worker()
        tree = Disclosure.of(owner)
        self.assertEqual(
            owner.compile("route"),
            {
                "kind": "role",
                "name": "worker",
                "description": "Complete the assigned work.",
            },
        )
        self.assertEqual(
            tree.skeleton(),
            {
                "roles": [
                    {
                        "kind": "role",
                        "name": "worker",
                        "description": "Complete the assigned work.",
                        "ref": "worker",
                    }
                ],
                "skills": [],
                "tools": [],
            },
        )
        self.assertEqual(
            owner.post_process.compile("route"),
            {
                "kind": "role",
                "name": "preserve-results",
                "description": "Make completed work reusable.",
            },
        )

    def test_active_owner_has_one_normal_card_and_string_designation(self) -> None:
        tree = Disclosure.of(role(post_process=Results(), children=[role("child")]))
        opened = tree.open("worker")
        instructions = opened["instructions"]
        self.assertEqual(
            {key: value for key, value in opened.items() if key != "instructions"},
            {
                "kind": "role",
                "name": "worker",
                "description": "The worker responsibility.",
                "ref": "worker",
                "post_process": POST_REF,
                "roles": [
                    {
                        "kind": "role",
                        "name": "child",
                        "description": "The child responsibility.",
                        "ref": "worker/child",
                    },
                    {
                        "kind": "role",
                        "name": "preserve-results",
                        "description": "Make completed work reusable.",
                        "ref": POST_REF,
                    },
                ],
                "skills": [],
                "tools": [],
            },
        )
        self.assertIsInstance(opened["post_process"], str)
        self.assertTrue(instructions.startswith(f"{OWNER_TEXT}\n\n{HEAD}\n{POST_LABEL}\n"))
        self.assertTrue(instructions.endswith(TAIL))
        for fragment in (
            OPEN_TOOL,
            repr(POST_REF),
            "before finishing",
            "does not execute",
            "establish success",
            "approvals",
            "blocked",
            "fails",
            "awaits approval",
            "rather than claiming success",
            "bypassing approval",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, instructions)

    def test_pre_process_is_composed_first_and_sends_the_agent_back(self) -> None:
        tree = Disclosure.of(role(pre_process=Prepare()))
        opened = tree.open("worker")
        instructions = opened["instructions"]
        self.assertEqual(opened["pre_process"], PRE_REF)
        self.assertNotIn("post_process", opened)
        self.assertTrue(instructions.startswith(f"{HEAD}\n{PRE_LABEL}\n{REQUIRED} "))
        self.assertTrue(instructions.endswith(f"{TAIL}\n\n{OWNER_TEXT}"))
        for fragment in (
            f"ref={PRE_REF!r}",
            "before starting",
            "return to this role's own instructions",
            "carry on",
            "blocked or fails",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, instructions)

    def test_both_contracts_sandwich_untouched_business_text(self) -> None:
        owner = role(pre_process=Prepare(), post_process=Results())
        opened = Disclosure.of(owner).open("worker")
        instructions = opened["instructions"]
        self.assertEqual(opened["pre_process"], PRE_REF)
        self.assertEqual(opened["post_process"], POST_REF)
        self.assertEqual(instructions.count(HEAD), 2)
        self.assertEqual(instructions.count(TAIL), 2)
        self.assertEqual(instructions.count(OWNER_TEXT), 1)

        before, business, after = instructions.split("\n\n", 2)
        self.assertIn(PRE_LABEL, before)
        self.assertEqual(business, OWNER_TEXT)
        self.assertIn(POST_LABEL, after)
        self.assertLess(instructions.index(PRE_LABEL), instructions.index(OWNER_TEXT.strip()))
        self.assertLess(instructions.index(OWNER_TEXT.strip()), instructions.index(POST_LABEL))
        self.assertEqual(owner.instructions, OWNER_TEXT)
        self.assertEqual(
            [card["ref"] for card in opened["roles"]], [PRE_REF, POST_REF]
        )

    def test_procedures_and_equipment_wait_until_opened(self) -> None:
        tree = compile_application(Contexture(name="process", roots=(Worker,))).disclosure
        owner = tree.open("worker")
        owner_text = json.dumps(owner)
        for hidden in (PROCEDURE, METHOD, "approved_evidence", f"{POST_REF}/preserve"):
            self.assertNotIn(hidden, owner_text)
        self.assertEqual([card["name"] for card in owner["skills"]], ["owner-method"])
        self.assertEqual([card["name"] for card in owner["tools"]], ["owner-tool"])
        process = tree.open(POST_REF)
        self.assertEqual(process["instructions"], PROCEDURE)
        self.assertNotIn("post_process", process)
        self.assertEqual(
            [card["ref"] for card in process["skills"]], [f"{POST_REF}/evidence"]
        )
        self.assertNotIn(METHOD, json.dumps(process))
        tool_card = process["tools"][0]
        self.assertEqual(tool_card["ref"], f"{POST_REF}/preserve")
        self.assertIn("approved_evidence", tool_card["input_schema"]["properties"])
        self.assertEqual(tree.open(f"{POST_REF}/evidence")["instructions"], METHOD)

    def test_repeated_open_does_not_mutate_business_text_or_reuse_payloads(self) -> None:
        owner = Worker()
        tree = Disclosure.of(owner)
        expected = tree.open("worker")
        changed = tree.open("worker")
        changed["instructions"] = "caller mutation"
        changed["roles"].clear()
        for _ in range(3):
            tree.open(POST_REF)
            self.assertEqual(tree.open("worker"), expected)
            self.assertEqual(tree.open("worker")["instructions"].count(HEAD), 1)
        self.assertEqual(owner.instructions, OWNER_TEXT)
        self.assertEqual(owner.post_process.instructions, PROCEDURE)

    def test_contract_ref_comes_from_the_view_not_a_guessed_path(self) -> None:
        class AliasedDisclosure(Disclosure):
            def ref_of(self, node):
                return f"surface:{super().ref_of(node)}"

        tree = AliasedDisclosure(Index.of(Worker))
        opened = tree.open("worker")
        actual_ref = f"surface:{POST_REF}"
        self.assertEqual(opened["post_process"], actual_ref)
        self.assertEqual(opened["roles"][1]["ref"], actual_ref)
        self.assertIn(f"ref={actual_ref!r}", opened["instructions"])
        self.assertNotIn(f"ref={POST_REF!r}", opened["instructions"])

    def test_custom_cards_policy_cannot_silently_hide_a_process_member(self) -> None:
        class WithoutProcess(Disclosure):
            def cards_of(self, nodes):
                return super().cards_of(
                    node
                    for node in nodes
                    if not isinstance(node, (PreProcess, PostProcess))
                )

        for slot, member, expected in (
            ("post_process", Results(name="private-finalizer"), "PostProcess"),
            ("pre_process", Prepare(name="private-setup"), "PreProcess"),
        ):
            with self.subTest(slot=slot):
                tree = WithoutProcess(Index.of(role(**{slot: member})))
                with self.assertRaisesRegex(ModelValidationError, "unavailable") as caught:
                    tree.open("worker")
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn(member.name, str(caught.exception))
                self.assertNotIn("worker", str(caught.exception))

    def test_direct_open_of_prompt_only_owner_fails_without_hidden_path(self) -> None:
        tree = Disclosure(Index.of(Worker), prompt_roots=frozenset({"worker"}))
        self.assertEqual(tree.skeleton(), {"roles": [], "skills": [], "tools": []})
        with self.assertRaisesRegex(ModelValidationError, "unavailable") as caught:
            tree.open("worker")
        for hidden in ("worker", "preserve-results", POST_REF, OWNER_TEXT, PROCEDURE):
            self.assertNotIn(hidden, str(caught.exception))
        self.assertEqual(tree.unrestricted().open("worker")["post_process"], POST_REF)


class ProcessTraversalTests(unittest.TestCase):
    def test_full_containment_walk_is_distinct_from_alternative_branch_axis(self) -> None:
        index = Index.of(Worker)
        refs = [
            "worker",
            "worker/child",
            POST_REF,
            f"{POST_REF}/review",
            f"{POST_REF}/evidence",
            f"{POST_REF}/preserve",
            "worker/owner-method",
            "worker/owner-tool",
        ]
        self.assertEqual([ref for ref, _ in index.walk()], refs)
        self.assertEqual(
            [ref for ref, _ in index.roles_by_level()], ["worker", "worker/child"]
        )
        self.assertEqual([ref for ref, _ in index.roles_with_refs()], refs[:4])
        self.assertEqual(
            [node.name for node in index.of_kind("role")],
            ["worker", "child", "preserve-results", "review"],
        )
        self.assertEqual(
            index.signpost(f"{POST_REF}/preserve"), (("worker", 1), (POST_REF, 1))
        )
        owner, process = index.find("worker"), index.find(POST_REF)
        self.assertEqual(index.children_of(owner), tuple(owner.members()))
        self.assertIs(index.parent_of(process), owner)
        self.assertIs(index.parent_of(index.find(f"{POST_REF}/preserve")), process)
        self.assertEqual(process.path, ("worker", "preserve-results"))

    def test_pre_process_leads_the_walk_without_joining_the_branch_axis(self) -> None:
        index = Index.of(role(pre_process=Prepare(), children=[role("child")]))
        self.assertEqual(
            [ref for ref, _ in index.walk()],
            ["worker", PRE_REF, f"{PRE_REF}/check", "worker/child"],
        )
        self.assertEqual(
            [ref for ref, _ in index.roles_by_level()], ["worker", "worker/child"]
        )

    def test_inspection_includes_nested_members_and_never_executes_tools(self) -> None:
        tree = Disclosure.of(
            role(
                pre_process=Prepare(),
                post_process=Results(post_process=Results(name="nested")),
            )
        )
        expected_refs = [ref for ref, _ in tree.index.walk()]
        self.assertCountEqual(list(inspection.every_ref(tree)), expected_refs)
        self.assertIn(f"{POST_REF}/nested/preserve", expected_refs)
        self.assertIn(PRE_REF, expected_refs)
        for ref in expected_refs:
            with self.subTest(ref=ref):
                step = inspection.open_step(tree, ref)
                self.assertFalse(step.refused)
                self.assertEqual(step.payload, tree.open(ref))
                self.assertEqual(json.loads(step.body), tree.open(ref))
        self.assertTrue(all(tool.calls == 0 for tool in tree.index.of_kind("tool")))

    def test_uses_are_normal_routing_cards_with_dependents_and_crossings(self) -> None:
        tree = Disclosure.of(
            [
                role(post_process=Results(uses=("shared/preserve",))),
                role("shared", tools=[Preserve()]),
            ]
        )
        opened = tree.open(POST_REF)
        self.assertEqual(opened["uses"], [tree.card_for("shared/preserve")])
        self.assertEqual(tree.index.uses_of(POST_REF), ("shared/preserve",))
        self.assertEqual(tree.index.dependents_of("shared/preserve"), (POST_REF,))
        self.assertEqual(
            list(tree.index.crossings()), [(POST_REF, "shared/preserve", "shared")]
        )
        self.assertNotIn("uses", opened["uses"][0])
        self.assertNotIn(
            "shared/preserve",
            [ref for ref, _ in tree.index.walk() if ref.startswith(f"{POST_REF}/")],
        )

    def test_unresolved_self_and_duplicate_uses_are_rejected(self) -> None:
        for refs, message in (
            (("missing/preserve",), "resolves to nothing"),
            ((POST_REF,), "itself"),
            (("shared/preserve", "shared/preserve"), "same reference twice"),
        ):
            with self.subTest(refs=refs):
                with self.assertRaisesRegex(ModelValidationError, message):
                    Index.of(role(post_process=Results(uses=refs)))

    def test_reference_cycles_are_cards_not_containment_cycles(self) -> None:
        tree = Disclosure.of(
            role(post_process=Results(uses=("worker",)), uses=(POST_REF,))
        )
        self.assertEqual(tree.open("worker")["uses"], [tree.card_for(POST_REF)])
        self.assertEqual(tree.open(POST_REF)["uses"], [tree.card_for("worker")])
        self.assertNotIn("post_process", tree.open(POST_REF)["uses"][0])
        self.assertEqual(tree.index.dependents_of(POST_REF), ("worker",))


class ProcessSelectionAndCompilationTests(unittest.TestCase):
    def test_selecting_owner_includes_the_complete_process_subtree(self) -> None:
        tree = Disclosure.of([Worker(), role("other")])
        selected = tree.select(SurfaceSelection.only("worker"))
        self.assertEqual([card["ref"] for card in selected.skeleton()["roles"]], ["worker"])
        self.assertEqual(selected.open("worker")["post_process"], POST_REF)
        self.assertEqual(selected.open(POST_REF)["instructions"], PROCEDURE)
        self.assertEqual(selected.open(f"{POST_REF}/evidence")["instructions"], METHOD)
        self.assertEqual(selected.open(f"{POST_REF}/preserve")["kind"], "tool")
        self.assertEqual(
            [ref for ref, _ in selected.selected_graph().walk()],
            [ref for ref, _ in tree.index.walk() if ref != "other"],
        )
        with self.assertRaises(RootOutsideSelectionError):
            selected.open("other")

    def test_selecting_the_process_member_promotes_it_without_its_owner(self) -> None:
        selected = Disclosure.of(Worker).select(SurfaceSelection.only(POST_REF))
        self.assertEqual(
            selected.skeleton(),
            {
                "roles": [
                    {
                        "kind": "role",
                        "name": "preserve-results",
                        "description": "Make completed work reusable.",
                        "ref": POST_REF,
                    }
                ],
                "skills": [],
                "tools": [],
            },
        )
        opened = selected.open(POST_REF)
        self.assertEqual(opened["instructions"], PROCEDURE)
        self.assertNotIn("post_process", opened)
        self.assertNotIn(OWNER_TEXT, json.dumps(opened))
        graph = selected.selected_graph()
        self.assertIsNone(graph.parent_of(graph.find(POST_REF)))
        self.assertEqual(
            [ref for ref, _ in graph.walk()],
            [POST_REF, f"{POST_REF}/review", f"{POST_REF}/evidence", f"{POST_REF}/preserve"],
        )
        for hidden in ("worker", "worker/child", "worker/owner-method", "worker/owner-tool"):
            with self.subTest(hidden=hidden):
                with self.assertRaises(RootOutsideSelectionError):
                    selected.open(hidden)

    def test_uses_never_widen_selection_or_reveal_prompt_only_targets(self) -> None:
        index = Index.of(
            [
                role(post_process=Results(uses=("shared/preserve", "private/preserve"))),
                role("shared", tools=[Preserve()]),
                role("private", tools=[Preserve()]),
            ]
        )
        tree = Disclosure(index, prompt_roots=frozenset({"private"}))
        self.assertEqual(tree.open(POST_REF)["uses"], [tree.card_for("shared/preserve")])
        selected = tree.select(SurfaceSelection.only(POST_REF))
        self.assertEqual(selected.open(POST_REF)["uses"], [])
        self.assertNotIn("private", json.dumps(selected.open(POST_REF)))
        self.assertEqual(selected.selected_graph().uses_of(POST_REF), ())
        with self.assertRaises(RootOutsideSelectionError):
            selected.open("shared/preserve")
        widened = tree.select(SurfaceSelection.only([POST_REF, "shared/preserve"]))
        self.assertEqual(widened.open(POST_REF)["uses"], [tree.card_for("shared/preserve")])
        self.assertFalse(widened.model_can_see("private/preserve"))

    def test_lazy_class_declaration_builds_fresh_nodes_and_stamps_channels(self) -> None:
        built = []

        class CountedWorker(Worker):
            def __init__(self) -> None:
                super().__init__()
                built.append(self)

        app = Contexture(name="process", roots=(CountedWorker,), channels=Channels)
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
        self.assertTrue(
            all(node.channels is replacement for _, node in Index.of(manager).walk())
        )

    def test_disclosure_only_compile_keeps_process_equipment_unbound(self) -> None:
        compiled = compile_disclosure_application(
            Contexture(name="process", roots=(Worker,))
        )
        self.assertFalse(compiled.index.is_bound)
        self.assertEqual(compiled.disclosure.open("worker")["post_process"], POST_REF)
        opened = compiled.disclosure.open(POST_REF)
        self.assertEqual(opened["instructions"], PROCEDURE)
        tool = compiled.disclosure.open(f"{POST_REF}/preserve")
        self.assertEqual(
            tool,
            {
                "kind": "tool",
                "name": "preserve",
                "description": "Preserve approved results.",
                "ref": f"{POST_REF}/preserve",
            },
        )
        self.assertEqual(opened["tools"], [tool])
        with self.assertRaisesRegex(ModelValidationError, "no executable bindings"):
            compiled.index.binding_of(f"{POST_REF}/preserve")
        self.assertEqual(compiled.index.find(f"{POST_REF}/preserve").calls, 0)

    def test_concurrent_compiles_and_opens_are_idempotent_and_isolated(self) -> None:
        app = Contexture(name="process", roots=(Worker,))

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
            self.assertEqual(payloads[0]["instructions"].count(HEAD), 1)
            for _, node in compiled.index.walk():
                self.assertNotIn(id(node), seen)
                seen.add(id(node))
            self.assertEqual(compiled.index.find("worker").instructions, OWNER_TEXT)
            self.assertEqual(compiled.index.find(f"{POST_REF}/preserve").calls, 0)
        results[0][1][0]["roles"].clear()
        self.assertTrue(results[0][1][1]["roles"])
        self.assertTrue(results[1][1][0]["roles"])


@pytest.fixture(params=[("pre_process", PreProcess), ("post_process", PostProcess)])
def phase(request):
    return request.param


def process_for(phase, name="process", **kwargs):
    return phase[1](
        name=name, description="Perform this phase.", instructions=PROCEDURE, **kwargs,
    )


def owner_for(phase, process=None, **kwargs):
    return role(**{phase[0]: process if process is not None else process_for(phase)}, **kwargs)


def test_symmetric_slot_validation_and_namespace(phase):
    slot, kind = phase
    for invalid in (kind, role(), Evidence(), Preserve(), object(), 1):
        with pytest.raises(ModelValidationError, match=f"constructed {kind.__name__}"):
            role(**{slot: invalid})
    for field, sibling in (
        ("children", role("process")),
        ("skills", Evidence("process")),
        ("tools", Preserve("process")),
    ):
        with pytest.raises(DuplicateNameError):
            owner_for(phase, **{field: [sibling]})
    with pytest.raises(DuplicateNameError):
        role(pre_process=Prepare(name="same"), post_process=Results(name="same"))


def test_symmetric_containment_sharing_cycles_and_separators(phase):
    slot, _ = phase
    shared = process_for(phase)
    with pytest.raises(ModelValidationError):
        Index.of([role("left", **{slot: shared}), role("right", **{slot: shared})])
    tool = Preserve()
    with pytest.raises(ModelValidationError):
        Index.of(owner_for(
            phase, process_for(phase, tools=[tool]),
            children=[process_for(phase, name="other", tools=[tool])],
        ))
    owner = owner_for(phase)
    getattr(owner, slot).children.append(owner)
    with pytest.raises(ModelValidationError, match="contains itself"):
        Index.of(owner)
    outer, inner = process_for(phase), process_for(phase, name="nested")
    setattr(outer, slot, inner)
    setattr(inner, slot, outer)
    with pytest.raises(ModelValidationError, match="contains itself"):
        Index.of(owner_for(phase, outer))
    for process in (
        process_for(phase, name="bad/name"),
        process_for(phase, tools=[Preserve("bad/tool")]),
    ):
        with pytest.raises(ModelValidationError, match="separates"):
            Index.of(owner_for(phase, process))


def test_symmetric_nested_contracts_are_explicit_and_not_inherited(phase):
    slot, _ = phase
    nested = process_for(phase, name="nested")
    process = process_for(phase, **{slot: nested}, children=[role("review")])
    tree = Disclosure.of(owner_for(phase, process, children=[role("child")]))
    assert tree.open("worker/process")[slot] == "worker/process/nested"
    assert tree.open("worker/process")["instructions"].count(HEAD) == 1
    for ref in ("worker/process/nested", "worker/process/review", "worker/child"):
        opened = tree.open(ref)
        assert "pre_process" not in opened
        assert "post_process" not in opened
        assert HEAD not in opened["instructions"]
        assert getattr(tree.index.find(ref), slot) is None
    assert tree.open("worker/process/nested")["instructions"] == PROCEDURE


def test_symmetric_route_inspect_and_deferred_equipment(phase):
    slot, _ = phase
    from tests.serving import compiled

    tree = Disclosure(compiled(owner_for(
        phase, process_for(phase, skills=[Evidence()], tools=[Preserve()]),
    )))
    owner = tree.open("worker")
    assert owner[slot] == "worker/process"
    assert [card["ref"] for card in owner["roles"]] == ["worker/process"]
    route = tree.index.find("worker").compile("route")
    assert route == {
        "kind": "role", "name": "worker", "description": "The worker responsibility.",
    }
    inspected = tree.inspect(["worker", "worker/process"])
    assert inspected["items"][0]["members"]["roles"][0]["ref"] == "worker/process"
    for payload in (route, tree.skeleton(), inspected):
        text = json.dumps(payload, ensure_ascii=False)
        for hidden in (slot, HEAD, OWNER_TEXT, PROCEDURE, METHOD, "input_schema"):
            assert hidden not in text
    for hidden in (PROCEDURE, METHOD, "approved_evidence", "worker/process/preserve"):
        assert hidden not in json.dumps(owner)
    opened = tree.open("worker/process")
    assert opened["instructions"] == PROCEDURE
    assert opened["skills"][0]["ref"] == "worker/process/evidence"
    assert METHOD not in json.dumps(opened)
    assert opened["tools"][0]["ref"] == "worker/process/preserve"
    assert "approved_evidence" in opened["tools"][0]["input_schema"]["properties"]
    assert tree.open("worker/process/evidence")["instructions"] == METHOD


def test_symmetric_view_alias_and_hidden_audience(phase):
    slot, kind = phase

    class AliasedDisclosure(Disclosure):
        def ref_of(self, node):
            return f"surface:{super().ref_of(node)}"

    index = Index.of(owner_for(phase))
    opened = AliasedDisclosure(index).open("worker")
    ref = "surface:worker/process"
    assert opened[slot] == ref
    assert opened["roles"][0]["ref"] == ref
    assert f"ref={ref!r}" in opened["instructions"]
    assert "ref='worker/process'" not in opened["instructions"]
    hidden = Disclosure(index, prompt_roots=frozenset({"worker"}))
    with pytest.raises(ModelValidationError, match="unavailable") as caught:
        hidden.open("worker")
    assert kind.__name__ in str(caught.value)
    for secret in ("worker", "worker/process", OWNER_TEXT, PROCEDURE):
        assert secret not in str(caught.value)
    assert hidden.unrestricted().open("worker")[slot] == "worker/process"


@pytest.mark.parametrize("hidden_slot", ["pre_process", "post_process"])
def test_either_hidden_member_refuses_the_entire_dual_contract(hidden_slot):
    owner = role(pre_process=Prepare(), post_process=Results())
    hidden = getattr(owner, hidden_slot)

    class Filtered(Disclosure):
        def cards_of(self, nodes):
            return super().cards_of(node for node in nodes if node is not hidden)

    with pytest.raises(ModelValidationError, match="unavailable") as caught:
        Filtered(Index.of(owner)).open("worker")
    for secret in (hidden.name, "worker", PRE_REF, POST_REF, OWNER_TEXT, PROCEDURE, SETUP):
        assert secret not in str(caught.value)


def test_symmetric_selection_uses_and_promoted_subtrees(phase):
    slot, _ = phase
    process = process_for(
        phase, children=[role("review")], skills=[Evidence()], tools=[Preserve()],
        uses=("shared/preserve", "private/preserve"),
    )
    tree = Disclosure(Index.of([
        owner_for(phase, process, children=[role("child")]),
        role("shared", tools=[Preserve()]), role("private", tools=[Preserve()]),
    ]), prompt_roots=frozenset({"private"}))
    ref = "worker/process"
    subtree = [ref, f"{ref}/review", f"{ref}/evidence", f"{ref}/preserve"]
    selected = tree.select(SurfaceSelection.only("worker"))
    assert selected.open("worker")[slot] == ref
    for member_ref in subtree:
        assert selected.open(member_ref)["ref"] == member_ref
    promoted = tree.select(SurfaceSelection.only(ref))
    assert [card["ref"] for card in promoted.skeleton()["roles"]] == [ref]
    assert [path for path, _ in promoted.selected_graph().walk()] == subtree
    assert promoted.selected_graph().parent_of(process) is None
    assert promoted.open(ref)["instructions"] == PROCEDURE
    assert slot not in promoted.open(ref)
    assert promoted.open(ref)["uses"] == []
    assert promoted.selected_graph().uses_of(ref) == ()
    for hidden in ("worker", "worker/child", "shared/preserve", "private/preserve"):
        with pytest.raises(RootOutsideSelectionError):
            promoted.open(hidden)
    assert tree.open(ref)["uses"] == [tree.card_for("shared/preserve")]
    assert tree.index.dependents_of("shared/preserve") == (ref,)
    assert (ref, "shared/preserve", "shared") in list(tree.index.crossings())
    widened = tree.select(SurfaceSelection.only([ref, "shared/preserve"]))
    assert widened.open(ref)["uses"] == [tree.card_for("shared/preserve")]
    assert not widened.model_can_see("private/preserve")


def test_symmetric_reference_validation_and_non_containment_cycles(phase):
    for refs, message in (
        (("missing/tool",), "resolves to nothing"),
        (("worker/process",), "itself"),
        (("shared/tool", "shared/tool"), "same reference twice"),
    ):
        with pytest.raises(ModelValidationError, match=message):
            Index.of(owner_for(phase, process_for(phase, uses=refs)))
    tree = Disclosure.of(owner_for(
        phase, process_for(phase, uses=("worker",)), uses=("worker/process",),
    ))
    assert tree.open("worker")["uses"] == [tree.card_for("worker/process")]
    assert tree.open("worker/process")["uses"] == [tree.card_for("worker")]
    assert phase[0] not in tree.open("worker/process")["uses"][0]


def test_symmetric_compile_freshness_channels_and_payload_isolation(phase):
    class WorkerWithProcess(Role):
        def __init__(self):
            super().__init__(
                name="worker", description="Work.", instructions=OWNER_TEXT,
                **{phase[0]: process_for(phase, tools=[Preserve()])},
            )

    app = Contexture(name="process", roots=(WorkerWithProcess,), channels=Channels)
    with ThreadPoolExecutor(max_workers=4) as pool:
        builds = list(pool.map(lambda _: compile_application(app), range(4)))
    seen = set()
    expected = builds[0].disclosure.open("worker")
    for build in builds:
        tree = build.disclosure
        assert tree.open("worker") == expected
        assert tree.open("worker")["instructions"].count(HEAD) == 1
        mutated = tree.open("worker")
        mutated["roles"].clear()
        mutated["instructions"] = "changed"
        assert tree.open("worker") == expected
        assert tree.index.find("worker").instructions == OWNER_TEXT
        for ref, node in build.index.walk():
            assert id(node) not in seen
            seen.add(id(node))
            assert node.channels is build.index.channels
            assert node.path == tuple(ref.split("/"))
            assert not inspection.open_step(tree, ref).refused
        assert build.index.find("worker/process/preserve").calls == 0
    manager = ControllerManager(channels=Channels())
    manager.register_role(WorkerWithProcess)
    replacement = Channels()
    manager.rebind_channels(replacement)
    assert all(node.channels is replacement for _, node in Index.of(manager).walk())
