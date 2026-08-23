"""One invocation runtime is shared by every Host surface."""

from __future__ import annotations

import asyncio
import unittest

from contexture import Contexture, Principal, Role, Tool, current_principal
from contexture.core.errors import WrongDoorError
from contexture.server import compile_application


class Who(Tool):
    def __init__(self) -> None:
        super().__init__(name="who", description="Return the caller.", read_only=True)

    async def invoke(self, suffix: str = "") -> str:
        who = current_principal()
        await asyncio.sleep(0)
        return f"{who.subject if who else 'anonymous'}{suffix}"


class Change(Tool):
    def __init__(self) -> None:
        super().__init__(name="change", description="Change a value.")

    async def invoke(self, value: str) -> dict:
        who = current_principal()
        return {"value": value, "actor": who.subject if who else None}


class Root(Role):
    def __init__(self) -> None:
        super().__init__(name="root", description="Runtime fixture.",
                         instructions="Read, then write.", tools=[Who(), Change()])


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        return compile_application(Contexture(name="runtime", roots=(Root,))).runtime()

    async def test_it_binds_one_explicit_principal_for_exactly_one_call(self) -> None:
        runtime = self.runtime()
        result = await runtime.invoke_read_only(
            "root/who", {"suffix": "!"}, principal=Principal(subject="alice")
        )
        self.assertEqual(result, "alice!")
        self.assertIsNone(current_principal())

    async def test_concurrent_callers_never_cross(self) -> None:
        runtime = self.runtime()
        results = await asyncio.gather(*(
            runtime.invoke_read_only("root/who", principal=Principal(subject=name))
            for name in ("alice", "bob", "carol")
        ))
        self.assertEqual(results, ["alice", "bob", "carol"])

    async def test_wrong_door_is_structured_for_a_non_agent_host(self) -> None:
        with self.assertRaises(WrongDoorError) as caught:
            await self.runtime().invoke_read_only("root/change", {"value": "x"})
        self.assertEqual(caught.exception.ref, "root/change")
        self.assertFalse(caught.exception.read_only)


if __name__ == "__main__":
    unittest.main()
