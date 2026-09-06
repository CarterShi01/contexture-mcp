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
    SurfaceSelection,
    bound_root_selection,
)
from contexture.core.model.system_api import SystemAPI
from contexture.server import (
    ContextureServer,
    HeaderRootSelector,
    HeaderSurfaceSelector,
    ServeError,
)

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
    def test_exact_paths_are_validated_and_reduced_to_an_antichain(self) -> None:
        index = tree().index
        selected = SurfaceSelection.only(
            ["alpha", "alpha/child", "alpha/child/inspect"]
        ).resolve(index)
        self.assertEqual(selected.names, frozenset({"alpha"}))
        with self.assertRaises(RootSelectionError) as caught:
            RootSelection.only(["missing"]).resolve(index)
        self.assertNotIn("alpha", str(caught.exception))

    def test_empty_and_non_segment_wildcards_are_rejected(self) -> None:
        for names in ([], ["alpha/**"], ["alpha/ch*"], ["alpha/*/inspect"]):
            with self.subTest(names=names), self.assertRaises(RootSelectionError):
                RootSelection.only(names)

    def test_direct_child_glob_expands_without_crossing_a_separator(self) -> None:
        selected = SurfaceSelection.only("alpha/*").resolve(tree().index)
        self.assertEqual(selected.names, frozenset({"alpha/child"}))
        self.assertTrue(selected.contains_ref("alpha/child/inspect"))
        self.assertFalse(selected.contains_ref("alpha"))

    def test_intersection_is_path_aware_monotonic_and_commutative(self) -> None:
        alpha = RootSelection.only(["alpha"])
        child = RootSelection.only(["alpha/child"])
        both = RootSelection.only(["alpha", "beta"])
        self.assertEqual(alpha.intersect(both), both.intersect(alpha))
        self.assertEqual(alpha.intersect(child), child)
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

    async def test_a_selected_descendant_is_promoted_without_disclosing_ancestors(self) -> None:
        selected = tree().select(SurfaceSelection.only(["alpha/child"]))
        roots = selected.skeleton()
        self.assertEqual(
            [(card["name"], card["ref"]) for card in roots["roles"]],
            [("child", "alpha/child")],
        )
        opened = selected.open("alpha/child")
        self.assertEqual([card["name"] for card in opened["tools"]], ["inspect"])
        with self.assertRaises(RootOutsideSelectionError):
            selected.open("alpha")

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

    async def test_selected_graph_promotes_anchor_and_hides_parent(self) -> None:
        selected = tree().select(SurfaceSelection.only("alpha/child"))
        graph = selected.selected_graph()
        self.assertEqual(
            [graph.ref_of(root) for root in graph.roots], ["alpha/child"]
        )
        child = graph.find("alpha/child")
        self.assertIsNone(graph.parent_of(child))
        self.assertEqual(
            [ref for ref, _ in graph.walk()],
            ["alpha/child", "alpha/child/inspect"],
        )

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
    def test_server_accepts_new_and_legacy_keywords_but_not_both(self) -> None:
        index = tree().index
        ContextureServer(index, surface_selector=HeaderSurfaceSelector())
        ContextureServer(index, root_selector=HeaderRootSelector())
        with self.assertRaises(ServeError):
            ContextureServer(
                index,
                surface_selector=HeaderSurfaceSelector(),
                root_selector=HeaderRootSelector(),
            )

    def test_missing_header_is_all_and_legacy_header_is_case_insensitive(self) -> None:
        index = tree().index
        selector = HeaderRootSelector()
        self.assertIsNone(selector.select(index, None, None).names)
        selected = selector.select(index, {"contexture-roots": " beta, alpha,beta "}, None)
        self.assertEqual(selected.names, frozenset({"alpha", "beta"}))

    def test_new_header_selects_an_exact_path_or_direct_children(self) -> None:
        index = tree().index
        selector = HeaderSurfaceSelector()
        exact = selector.select(
            index, {"Contexture-Select": "alpha/child"}, None
        )
        wildcard = selector.select(index, {"Contexture-Select": "alpha/*"}, None)
        self.assertEqual(exact.names, frozenset({"alpha/child"}))
        self.assertEqual(wildcard, exact)

    def test_sending_new_and_legacy_headers_is_ambiguous(self) -> None:
        with self.assertRaises(RootSelectionError):
            HeaderSurfaceSelector().select(
                tree().index,
                {"Contexture-Select": "alpha", "Contexture-Roots": "beta"},
                None,
            )

    def test_ceiling_can_only_narrow_a_request(self) -> None:
        index = tree().index
        selector = HeaderRootSelector(
            ceiling=lambda principal: RootSelection.only(["alpha"])
        )
        selected = selector.select(
            index, {"Contexture-Select": "alpha/child,beta"}, None
        )
        self.assertEqual(selected.names, frozenset({"alpha/child"}))


if __name__ == "__main__":
    unittest.main()
