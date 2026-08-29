"""Transport-neutral execution of one compiled Controller forest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import WrongDoorError
from ..principal import Principal, bound
from .graph_context import bound_graph
from .index import Index
from .telemetry import InMemoryTelemetry, Telemetry, bound_telemetry, report


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    """Resolve, validate and invoke Tools independently of any Host protocol."""

    index: Index
    telemetry: Telemetry = field(default_factory=InMemoryTelemetry, repr=False)

    async def invoke_read_only(
        self, ref: str, arguments: dict[str, Any] | None = None, *,
        principal: Principal | None = None, context: Any = None,
    ) -> Any:
        return await self._invoke(ref, arguments, read_only=True,
                                  principal=principal, context=context)

    async def invoke(
        self, ref: str, arguments: dict[str, Any] | None = None, *,
        principal: Principal | None = None, context: Any = None,
    ) -> Any:
        return await self._invoke(ref, arguments, read_only=False,
                                  principal=principal, context=context)

    async def _invoke(
        self, ref: str, arguments: dict[str, Any] | None, *, read_only: bool,
        principal: Principal | None, context: Any,
    ) -> Any:
        tool = self.index.tool(ref)
        if tool.read_only is not read_only:
            raise WrongDoorError(ref=ref, read_only=tool.read_only)
        try:
            with (
                bound(principal),
                bound_graph(self.index),
                bound_telemetry(self.telemetry),
            ):
                result = await self.index.binding_of(ref).call(arguments, context)
        except Exception:
            report(self.telemetry, ref, failed=True)
            raise
        report(self.telemetry, ref)
        return result


__all__ = ["ApplicationRuntime"]
