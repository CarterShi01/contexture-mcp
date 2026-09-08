"""A downstream program type-checked against the built wheel in CI."""

from __future__ import annotations

from contexture import Contexture, Publication, Role, Skill, Tool
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


class OperationSummary(Publication):
    def __init__(self) -> None:
        super().__init__(
            name="summary",
            description="Prepare reusable deployment findings.",
            instructions="Read the status and report findings with evidence.",
            skills=[Explain()],
            tools=[Status()],
        )


class Operations(Role):
    def __init__(self) -> None:
        super().__init__(
            name="operations",
            description="Operate deployments.",
            instructions="Choose the smallest relevant capability.",
            skills=[Explain()],
            tools=[Status()],
            publication=OperationSummary(),
        )


app = Contexture(name="consumer", roots=(Operations,))
options = ContextureOptions(transport="stdio")

assert app.name == "consumer"
assert options.transport == "stdio"
