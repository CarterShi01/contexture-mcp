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
