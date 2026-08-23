"""Transport-neutral execution of one compiled Controller forest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..errors import WrongDoorError
from ..principal import Principal, bound
from .index import Index


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    """Resolve, validate and invoke Tools independently of any Host protocol."""

    index: Index

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
        with bound(principal):
            return await self.index.binding_of(ref).call(arguments, context)


__all__ = ["ApplicationRuntime"]
