"""A downstream program type-checked against the built wheel in CI."""

from __future__ import annotations

from typing import assert_type

from contexture import (
    Contexture, PostProcess, PreProcess, Role, Skill, Tool, binding_instruction,
)
from contexture.server import ContextureOptions


class Explain(Skill):
    def __init__(self) -> None:
        super().__init__(
            name="explain",
            description="Explain one deployment.",
            instructions="Inspect the deployment before explaining it.",
        )


class Status(Tool):
    def __init__(self) -> None:
        super().__init__(
            name="status",
            description="Read deployment status.",
            read_only=True,
        )

    async def invoke(self, deployment: str) -> dict[str, str]:
        return {"deployment": deployment, "status": "ready"}


class OperationSummary(PostProcess):
    def __init__(self) -> None:
        super().__init__(
            name="summary",
            description="Prepare reusable deployment findings.",
            instructions="Read the status and report findings with evidence.",
            skills=[Explain()],
            tools=[Status()],
        )


class Readiness(PreProcess):
    def __init__(self) -> None:
        super().__init__(
            name="readiness",
            description="Check deployment readiness.",
            instructions="Read the deployment status before starting work.",
            tools=[Status()],
        )


class Operations(Role):
    def __init__(self) -> None:
        super().__init__(
            name="operations",
            description="Operate deployments.",
            instructions=binding_instruction(
                "operations policy", "Choose the smallest relevant capability.",
                action="Check readiness first.",
            ),
            skills=[Explain()],
            tools=[Status()],
            pre_process=Readiness(),
            post_process=OperationSummary(),
        )


app = Contexture(name="consumer", roots=(Operations,))
options = ContextureOptions(transport="stdio")

assert app.name == "consumer"
assert options.transport == "stdio"

owner = Operations()
assert_type(owner.pre_process, PreProcess | None)
assert_type(owner.post_process, PostProcess | None)
assert_type(binding_instruction("operations", "Use evidence."), str)
