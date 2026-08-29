"""One disclosure graph with framework-owned usage telemetry beside it."""

from __future__ import annotations

import asyncio
import unittest

from contexture import (
    Contexture,
    Role,
    Skill,
    Tool,
    current_graph,
    current_telemetry,
)
from contexture.server import NodeUsage, compile_application


class Status(Tool):
    def __init__(self) -> None:
        super().__init__(name="status", description="Read scheduler status.", read_only=True)

    async def invoke(self) -> str:
        return "ok"


class Fail(Tool):
    def __init__(self) -> None:
        super().__init__(name="fail", description="Raise a fixture failure.", read_only=True)

    async def invoke(self) -> None:
        raise ValueError("fixture failure")


class GraphIdentity(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="graph-identity",
            description="Return whether this call sees its serving graph.",
            read_only=True,
        )

    async def invoke(self) -> bool:
        return current_graph().find("hermes") is not None


class Usage(Tool):
    def __init__(self) -> None:
        super().__init__(name="usage", description="Read framework usage.", read_only=True)

    async def invoke(self, ref: str) -> dict[str, object]:
        current_graph().find(ref)
        return current_telemetry().usage(ref).as_dict()


class Operate(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="operate",
            description="Operate schedules safely.",
            instructions="Inspect before changing a schedule.",
            uses=("hermes/scheduling/status",),
        )


class Scheduling(Role):
    def __init__(self) -> None:
        super().__init__(
            name="scheduling",
            description="Own scheduled work.",
            instructions="Route schedule work.",
            uses=("infra",),
            skills=[Operate()],
            tools=[Status(), Fail()],
        )


class Hermes(Role):
    def __init__(self) -> None:
        super().__init__(
            name="hermes",
            description="Own resident interaction and scheduling.",
            instructions="Choose one resident responsibility.",
            children=[Scheduling()],
            tools=[GraphIdentity(), Usage()],
        )


class Infra(Role):
    def __init__(self) -> None:
        super().__init__(
            name="infra",
            description="Own runtime infrastructure.",
            instructions="Inspect infrastructure.",
        )


class BrokenTelemetry:
    def record(self, ref: str, *, failed: bool = False) -> None:
        raise RuntimeError("exporter unavailable")

    def usage(self, ref: str) -> NodeUsage:
        return NodeUsage(ref=ref)


class TelemetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.compiled = compile_application(Contexture(name="oc", roots=(Hermes, Infra)))

    def test_role_skill_and_tool_share_one_dependency_contract(self) -> None:
        index = self.compiled.index

        self.assertEqual(index.uses_of("hermes/scheduling"), ("infra",))
        self.assertEqual(index.dependents_of("infra"), ("hermes/scheduling",))
        self.assertEqual(
            index.dependents_of("hermes/scheduling/status"),
            ("hermes/scheduling/operate",),
        )

    async def test_one_disclosure_keeps_telemetry_out_of_agent_context(self) -> None:
        opened = await self.compiled.server().surface.api.open("hermes/scheduling")

        self.assertEqual(
            [card["ref"] for card in (*opened["skills"], *opened["tools"])],
            [
                "hermes/scheduling/operate",
                "hermes/scheduling/status",
                "hermes/scheduling/fail",
            ],
        )
        self.assertEqual([card["ref"] for card in opened["uses"]], ["infra"])
        self.assertNotIn("dependents", opened)
        self.assertNotIn("call_count", opened)
        self.assertNotIn("telemetry", opened)

        usage = self.compiled.telemetry.usage("hermes/scheduling")
        self.assertEqual(usage.call_count, 1)
        self.assertEqual(usage.error_count, 0)
        self.assertIsNotNone(usage.last_used_at)

    async def test_tool_calls_and_errors_are_recorded_automatically(self) -> None:
        runtime = self.compiled.runtime()

        self.assertEqual(await runtime.invoke_read_only("hermes/scheduling/status"), "ok")
        with self.assertRaisesRegex(Exception, "Error executing tool fail"):
            await runtime.invoke_read_only("hermes/scheduling/fail")

        success = self.compiled.telemetry.usage("hermes/scheduling/status")
        failure = self.compiled.telemetry.usage("hermes/scheduling/fail")
        self.assertEqual((success.call_count, success.error_count), (1, 0))
        self.assertEqual((failure.call_count, failure.error_count), (1, 1))

    async def test_a_tool_can_query_telemetry_without_putting_it_in_disclosure(self) -> None:
        runtime = self.compiled.runtime()
        await runtime.invoke_read_only("hermes/scheduling/status")

        usage = await runtime.invoke_read_only(
            "hermes/usage", {"ref": "hermes/scheduling/status"}
        )

        self.assertEqual(usage["call_count"], 1)
        self.assertEqual(usage["error_count"], 0)

    async def test_concurrent_calls_are_counted_without_losing_updates(self) -> None:
        runtime = self.compiled.runtime()

        await asyncio.gather(*(
            runtime.invoke_read_only("hermes/scheduling/status")
            for _ in range(32)
        ))

        usage = self.compiled.telemetry.usage("hermes/scheduling/status")
        self.assertEqual((usage.call_count, usage.error_count), (32, 0))

    async def test_telemetry_export_failure_never_changes_business_outcome(self) -> None:
        compiled = compile_application(
            Contexture(name="oc", roots=(Hermes, Infra)), telemetry=BrokenTelemetry()
        )

        self.assertEqual(
            await compiled.runtime().invoke_read_only("hermes/scheduling/status"),
            "ok",
        )

    async def test_an_invoked_tool_sees_the_serving_graph_and_telemetry(self) -> None:
        result = await self.compiled.runtime().invoke_read_only("hermes/graph-identity")
        self.assertTrue(result)
        with self.assertRaisesRegex(RuntimeError, "No compiled Contexture graph"):
            current_graph()
        with self.assertRaisesRegex(RuntimeError, "No Contexture telemetry"):
            current_telemetry()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
