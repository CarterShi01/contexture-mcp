"""Root-selected request surfaces over one canonical Contexture Index."""

from __future__ import annotations

import asyncio
import unittest

from contexture import Role, Tool, current_graph
from contexture.core.model.disclosure import Disclosure
from contexture.core.model.root_selection import (
    RootOutsideSelectionError,
    RootSelection,
    RootSelectionError,
    bound_root_selection,
)
from contexture.core.model.system_api import SystemAPI
from contexture.server import HeaderRootSelector

from tests.serving import compiled


class Inspect(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="inspect", description="List the visible graph.", read_only=True
        )

    async def invoke(self) -> list[str]:
        return [ref for ref, _ in current_graph().walk()]


class AlphaChild(Role):
    def __init__(self) -> None:
        super().__init__(
            name="child",
            description="The complete child branch.",
            instructions="Inspect this branch.",
            tools=[Inspect()],
        )


class Alpha(Role):
    def __init__(self) -> None:
        super().__init__(
            name="alpha",
            description="The first root.",
            instructions="Enter the child branch.",
            children=[AlphaChild()],
        )


class BetaTool(Tool):
    def __init__(self) -> None:
        super().__init__(name="read", description="Read beta.", read_only=True)

    async def invoke(self) -> str:
        return "beta"


class Beta(Role):
    def __init__(self) -> None:
        super().__init__(
            name="beta",
            description="The second root.",
            instructions="Read beta.",
            tools=[BetaTool()],
        )


def tree(*, prompt_roots: frozenset[str] = frozenset()) -> Disclosure:
    return Disclosure(compiled((Alpha, Beta)), prompt_roots)


class RootSelectionValueTests(unittest.TestCase):
    def test_exact_roots_are_validated_without_listing_the_rest(self) -> None:
        index = tree().index
        self.assertEqual(RootSelection.only(["beta", "beta"]).resolve(index).names,
                         frozenset({"beta"}))
        with self.assertRaises(RootSelectionError) as caught:
            RootSelection.only(["missing"]).resolve(index)
        self.assertNotIn("alpha", str(caught.exception))

    def test_descendant_and_empty_selectors_are_not_root_selections(self) -> None:
        for names in ([], ["alpha/child"]):
            with self.subTest(names=names), self.assertRaises(RootSelectionError):
                RootSelection.only(names)

    def test_intersection_is_monotonic_commutative_and_idempotent(self) -> None:
        alpha = RootSelection.only(["alpha"])
        both = RootSelection.only(["alpha", "beta"])
        self.assertEqual(alpha.intersect(both), both.intersect(alpha))
        self.assertEqual(alpha.intersect(alpha), alpha)
        self.assertEqual(RootSelection.all().intersect(alpha), alpha)


class DisclosureSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_keeps_index_order_and_a_selected_tree_is_complete(self) -> None:
        selected = tree().select(RootSelection.only(["alpha"]))
        roots = selected.skeleton()
        self.assertEqual([card["name"] for card in roots["roles"]], ["alpha"])
        opened = selected.open("alpha")
        self.assertEqual([card["name"] for card in opened["roles"]], ["child"])
        nested = selected.open("alpha/child")
        self.assertEqual([card["name"] for card in nested["tools"]], ["inspect"])

    async def test_direct_open_and_invoke_cannot_bypass_the_selection(self) -> None:
        api = SystemAPI(tree().select(RootSelection.only(["alpha"])))
        with self.assertRaises(RootOutsideSelectionError):
            await api.open("beta")
        with self.assertRaises(RootOutsideSelectionError):
            await api.invoke_read_only("beta/read")

    async def test_unrestricted_removes_prompt_ownership_but_not_root_selection(self) -> None:
        selected = tree(prompt_roots=frozenset({"alpha"})).select(
            RootSelection.only(["alpha"])
        )
        unrestricted = selected.unrestricted()
        self.assertEqual(unrestricted.selection.names, frozenset({"alpha"}))
        with self.assertRaises(RootOutsideSelectionError):
            unrestricted.open("beta")

    async def test_current_graph_is_the_selected_facade(self) -> None:
        api = SystemAPI(tree().select(RootSelection.only(["alpha"])))
        visible = await api.invoke_read_only("alpha/child/inspect")
        self.assertEqual(visible, ["alpha", "alpha/child", "alpha/child/inspect"])

    async def test_concurrent_request_bindings_do_not_contaminate_each_other(self) -> None:
        disclosure = tree()

        async def names(name: str) -> list[str]:
            with bound_root_selection(RootSelection.only([name])):
                await asyncio.sleep(0)
                return [card["name"] for card in disclosure.skeleton()["roles"]]

        alpha, beta = await asyncio.gather(names("alpha"), names("beta"))
        self.assertEqual(alpha, ["alpha"])
        self.assertEqual(beta, ["beta"])


class HeaderSelectorTests(unittest.TestCase):
    def test_missing_header_is_compatibility_all_and_header_is_case_insensitive(self) -> None:
        index = tree().index
        selector = HeaderRootSelector()
        self.assertIsNone(selector.select(index, None, None).names)
        selected = selector.select(index, {"contexture-roots": " beta, alpha,beta "}, None)
        self.assertEqual(selected.names, frozenset({"alpha", "beta"}))

    def test_ceiling_can_only_narrow_a_request(self) -> None:
        index = tree().index
        selector = HeaderRootSelector(
            ceiling=lambda principal: RootSelection.only(["alpha"])
        )
        selected = selector.select(index, {"Contexture-Roots": "alpha,beta"}, None)
        self.assertEqual(selected.names, frozenset({"alpha"}))


if __name__ == "__main__":
    unittest.main()
