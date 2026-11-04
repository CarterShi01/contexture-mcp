"""Generated proxy Tools disclose and validate one explicit wire contract."""

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from contexture import Contexture, ExplicitSchemaTool, Role
from contexture.core.model.system_api import SystemAPI
from contexture.server import compile_application


class Proxy(ExplicitSchemaTool):
    def __init__(self) -> None:
        super().__init__(
            name="proxy",
            description="Call an externally described operation.",
            read_only=True,
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        )

    async def invoke(self, **arguments: object) -> object:
        return arguments


class Root(Role):
    def __init__(self) -> None:
        super().__init__(
            name="root",
            description="Hold a proxy.",
            instructions="Use the proxy contract.",
            tools=(Proxy(),),
        )


class RefusingProxy(Proxy):
    async def invoke(self, **arguments: object) -> object:
        del arguments
        raise PermissionError("proxy policy refused this call")


def test_explicit_schema_is_the_disclosed_and_validated_contract() -> None:
    compiled = compile_application(Contexture(name="proxy", roots=(Root,)))
    schema = compiled.index.schema_of(compiled.index.tool("root/proxy"))
    assert schema == {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    catalog = compiled.catalog(view="model", detail="definition")
    assert catalog.get("root/proxy").tool is not None
    assert catalog.get("root/proxy").as_json()["tool"]["input_schema"] == schema


@pytest.mark.asyncio
async def test_explicit_schema_rejects_before_running_the_proxy() -> None:
    compiled = compile_application(Contexture(name="proxy", roots=(Root,)))
    api = SystemAPI(compiled.disclosure)

    assert await api.invoke_read_only("root/proxy", {"query": "status"}) == {
        "query": "status"
    }
    with pytest.raises(ToolError, match="required property"):
        await api.invoke_read_only("root/proxy", {})
    with pytest.raises(ToolError, match="Additional properties"):
        await api.invoke_read_only("root/proxy", {"query": "ok", "extra": True})


@pytest.mark.asyncio
async def test_explicit_schema_preserves_business_permission_refusals() -> None:
    class RefusingRoot(Role):
        def __init__(self) -> None:
            super().__init__(
                name="root",
                description="Hold a refusing proxy.",
                instructions="Use the proxy contract.",
                tools=(RefusingProxy(),),
            )

    compiled = compile_application(Contexture(name="proxy", roots=(RefusingRoot,)))
    api = SystemAPI(compiled.disclosure)

    with pytest.raises(ToolError, match="proxy policy refused this call"):
        await api.invoke_read_only("root/proxy", {"query": "status"})
