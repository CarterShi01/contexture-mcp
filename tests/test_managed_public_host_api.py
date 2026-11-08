"""Public seams hosts need to compose managed runtimes from static Tools (2.0)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from contexture import Tool
from contexture.application.managed_generation import _settle, settle
from contexture.server.binding import TypeHintBinding


class Echo(Tool):
    async def invoke(self, text: str, times: int = 1) -> str:
        return text * times


def test_type_hint_call_contract_is_public_and_matches_call() -> None:
    binding = TypeHintBinding(Echo(name="echo", description="Echo"))
    contract = binding.call_contract
    assert contract.uses_resolvers is False and contract.context_kwarg is None
    decoded: Any = contract.validate_arguments({"text": "a", "times": "2"})
    assert decoded == {"text": "a", "times": 2}  # coerced exactly as call() would


@pytest.mark.asyncio
async def test_settle_is_public_and_the_private_alias_is_identical() -> None:
    assert _settle is settle
    assert await settle(asyncio.create_task(asyncio.sleep(0, result=7))) == 7


def test_managed_mcp_server_takes_the_same_auth_as_the_static_server() -> None:
    from contexture import Principal
    from contexture.server.identity import Auth
    from contexture.server.managed import ManagedMCPServer

    class Verifier:
        async def verify(self, token: str) -> Principal | None:
            return None

    auth = Auth(verifier=Verifier(), issuer="https://idp.example", resource="https://mcp.example/mcp")
    server = ManagedMCPServer(object())  # type: ignore[arg-type]  # build() does not touch the runtime
    wire = server.build(auth=auth)
    assert server.build() is wire and server.build(auth=auth) is wire
    other = Auth(verifier=Verifier(), issuer="https://idp.example", resource="https://mcp.example/mcp")
    with pytest.raises(ValueError):
        server.build(auth=other)


@pytest.mark.asyncio
async def test_selection_resolver_attenuates_each_managed_request() -> None:
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from mcp.server.mcpserver.exceptions import ToolError

    from contexture.server.managed import ManagedMCPServer

    seen = []

    class Runtime:
        @asynccontextmanager
        async def request(self, *, run_context=None, selection=None, publication_audience=False):
            seen.append(selection)

            class Request:
                async def discover(self):
                    return {"ok": True}
            yield Request()

    ctx = SimpleNamespace(request_context=SimpleNamespace(request=None, meta={}))
    plain = ManagedMCPServer(Runtime())  # type: ignore[arg-type]
    assert await plain._call(ctx, "discover") == {"ok": True}  # type: ignore[arg-type]
    selected = ManagedMCPServer(Runtime(), selection_resolver=lambda _ctx: frozenset({"team"}))  # type: ignore[arg-type]
    assert await selected._call(ctx, "discover") == {"ok": True}  # type: ignore[arg-type]

    async def async_resolver(_ctx):
        return frozenset({"work"})
    asynchronous = ManagedMCPServer(Runtime(), selection_resolver=async_resolver)  # type: ignore[arg-type]
    await asynchronous._call(ctx, "discover")  # type: ignore[arg-type]
    assert seen == [None, frozenset({"team"}), frozenset({"work"})]
    wrong = ManagedMCPServer(Runtime(), selection_resolver=lambda _ctx: ["team"])  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="permission_denied"):
        await wrong._call(ctx, "discover")  # type: ignore[arg-type]

