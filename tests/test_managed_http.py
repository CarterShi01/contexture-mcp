"""Local managed candidate over real SDK HTTP, not production authentication.

The Host authority below is the existing FileJournal runtime test fixture.
Only explicit MCP metadata is exercised; no HTTP run/header negotiation is added.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import socket
from typing import Any

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.transport_security import TransportSecuritySettings
import pytest
import pytest_asyncio
from sse_starlette.sse import AppStatus
import uvicorn

from contexture.core.model.system_api import GATEWAY
from contexture.server.managed import ManagedMCPServer, RUN_META_KEY
from tests.test_managed_runtime import (
    binding_switch, ref, setup, swap, system as system,
)


class ReadyServer(uvicorn.Server):
    """Signal actual uvicorn startup, without a guessed readiness sleep."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = asyncio.Event()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        self.ready.set()


@asynccontextmanager
async def serve(adapter: ManagedMCPServer) -> AsyncIterator[str]:
    """One bound socket and server task; close transport before runtime owner."""
    baseline = asyncio.all_tasks()
    prior_sse_exit = AppStatus.should_exit
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.setblocking(False)
        origin = f"http://127.0.0.1:{port}"
        app = adapter.build().streamable_http_app(
            host="127.0.0.1",
            stateless_http=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[f"127.0.0.1:{port}"],
                allowed_origins=[origin],
            ),
        )
        server = ReadyServer(uvicorn.Config(
            app, host="127.0.0.1", port=port, loop="asyncio", lifespan="on",
            access_log=False, log_level="warning", timeout_graceful_shutdown=5,
        ))
        task = asyncio.create_task(server.serve(sockets=[listener]), name="managed-http")
        ready = asyncio.create_task(server.ready.wait(), name="managed-http-ready")
        try:
            done, _ = await asyncio.wait(
                {task, ready}, timeout=15, return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                await task
                pytest.fail("HTTP server exited before readiness")
            assert ready in done and server.started, "HTTP startup timed out"
            yield origin + "/mcp"
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
            server.should_exit = True
            # Embedded uvicorn shutdown is not an OS signal. Notify the SSE
            # dependency's documented shutdown flag, then await its own watcher.
            AppStatus.should_exit = True
            watchers = [pending for pending in asyncio.all_tasks() - baseline
                        if pending.get_coro().__qualname__ == "_shutdown_watcher"]
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=10)
                if watchers:
                    await asyncio.wait_for(asyncio.gather(*watchers), timeout=2)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                AppStatus.should_exit = prior_sse_exit
            assert not server.server_state.tasks
            assert not server.server_state.connections
            assert all(not bound.is_serving() for bound in server.servers)
        # A real refused TCP connection, not just a closed Python socket handle.
        with pytest.raises(OSError):
            async with asyncio.timeout(2):
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                pytest.fail("HTTP listener survived graceful shutdown")
    # pytest-asyncio runs async-generator teardown in a new finalizer task.
    leaked = {task for task in asyncio.all_tasks() - baseline
              if not task.done() and task is not asyncio.current_task()}
    assert not leaked, f"HTTP fixture leaked tasks: {leaked}"


@pytest_asyncio.fixture
async def endpoint(system):
    coordinator, authority, runtime = await setup(system)
    expected_closed = Counter(system[2].resources.keys())
    try:
        async with serve(ManagedMCPServer(runtime)) as url:
            yield url, authority, coordinator
            closed_before_http_shutdown = list(system[2].closed)
        # Serving must not shut down application-owned generations.
        # A hot swap may already have legitimately retired generation A.
        assert system[2].closed == closed_before_http_shutdown
    finally:
        await coordinator.shutdown()
    # The swap prepares all four resources again; unchanged A bindings have
    # distinct handles but identical keys in Execution.resources (a dict).
    if ("B", "r") in system[2].resources:
        expected_closed.update(("B" if node == "r" else "A", node)
                               for node in ("r", "w", "p", "reserved"))
    assert Counter(system[2].closed) == expected_closed


@asynccontextmanager
async def connect(url: str, *, modern: bool = True,
                  headers: httpx2.Headers | dict[str, str] | None = None
                  ) -> AsyncIterator[tuple[ClientSession, list[str]]]:
    methods: list[str] = []

    async def observe(request: httpx2.Request) -> None:
        assert "mcp-session-id" not in request.headers
        if request.method == "POST":
            methods.append(json.loads(request.content)["method"])

    async def observe_response(response: httpx2.Response) -> None:
        assert "mcp-session-id" not in response.headers

    async with asyncio.timeout(30):
        async with httpx2.AsyncClient(
            trust_env=False, timeout=5,
            headers=headers,
            event_hooks={"request": [observe], "response": [observe_response]},
        ) as http:
            async with streamable_http_client(url, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as client:
                    if modern:
                        await client.discover()
                    else:
                        await client.initialize()
                    yield client, methods


async def call(client: ClientSession, tool: str, arguments: dict[str, Any],
               *, meta: types.RequestParamsMeta | None = None) -> types.CallToolResult:
    result = await client.call_tool(tool, arguments, meta=meta)
    assert isinstance(result, types.CallToolResult)
    return result


def payload(result: types.CallToolResult) -> dict[str, Any]:
    assert not result.is_error
    if result.structured_content is not None:
        return result.structured_content
    assert isinstance(result.content[0], types.TextContent)
    return json.loads(result.content[0].text)


def refused(result: types.CallToolResult, code: str) -> None:
    assert result.is_error and result.structured_content is None
    assert len(result.content) == 1
    assert isinstance(result.content[0], types.TextContent)
    assert result.content[0].text.split(": ", 1)[1] == f"{code}: Managed request refused"


@pytest.mark.asyncio
@pytest.mark.parametrize("modern", [False, True], ids=["initialize", "discover"])
async def test_http_handshake_fixed_five_open_and_invoke(endpoint, system, modern):
    url, _, _ = endpoint
    async with connect(url, modern=modern) as (client, methods):
        assert ("server/discover" if modern else "initialize") in methods
        tools = (await client.list_tools()).tools
        assert [tool.name for tool in tools] == [entry.name for entry in GATEWAY]
        assert len(tools) == 5
        roots = payload(await call(client, "contexture_discover", {}))["tools"]
        read = roots[0]["ref"]
        assert read == ref()
        assert payload(await call(client, "contexture_open", {"ref": read}))["read_only"]
        assert payload(await call(client, "contexture_invoke_read_only",
                                  {"ref": read, "arguments": {"count": "8"}}))["count"] == 8
        assert payload(await call(client, "contexture_invoke", {"ref": roots[1]["ref"]}))["version"] == "A"
        assert [c for c in system[2].calls if c[2] == "handler"] == [
            ("A", "r", "handler"), ("A", "w", "handler"),
        ]


@pytest.mark.asyncio
async def test_http_wrong_doors_refuse_before_native_dispatch(endpoint, system):
    url, _, _ = endpoint
    async with connect(url) as (client, _):
        roots = payload(await call(client, "contexture_discover", {}))["tools"]
        refused(await call(client, "contexture_invoke_read_only",
                           {"ref": roots[1]["ref"]}), "wrong_door")
        refused(await call(client, "contexture_invoke",
                           {"ref": roots[0]["ref"]}), "wrong_door")
        assert system[2].calls == []  # No decoder, validator or handler.


@pytest.mark.asyncio
async def test_http_same_client_swap_fresh_reference_and_current_revocation(endpoint, system):
    url, authority, coordinator = endpoint
    async with connect(url) as (client, methods):
        before = (await client.list_tools()).tools
        old = payload(await call(client, "contexture_discover", {}))["tools"][0]["ref"]
        assert payload(await call(client, "contexture_open", {"ref": old}))["input_schema"]["required"] == []
        binding_switch(system)
        await swap(system, coordinator)
        for tool in ("contexture_open", "contexture_invoke_read_only"):
            refused(await call(client, tool, {"ref": old}), "stale_reference")
        fresh = payload(await call(client, "contexture_discover", {}))["tools"][0]["ref"]
        assert fresh == ref(generation=102) and fresh != old
        schema = payload(await call(client, "contexture_open", {"ref": fresh}))["input_schema"]
        assert schema["required"] == ["count"]
        refused(await call(client, "contexture_invoke_read_only", {"ref": fresh}), "binding_contract_invalid")
        assert payload(await call(client, "contexture_invoke_read_only",
                                  {"ref": fresh, "arguments": {"count": 7}}))["version"] == "B"
        lists_before = methods.count("tools/list")
        assert (await client.list_tools()).tools == before
        # This SDK currently sends tools/list; this is not capability-change
        # notification evidence, nor a claim about other SDK caching policies.
        assert methods.count("tools/list") == lists_before + 1
        calls_before = list(system[2].calls)
        checks_before = len(authority.calls)
        authority.scope = frozenset()
        refused(await call(client, "contexture_invoke_read_only",
                           {"ref": fresh, "arguments": {"count": 7}}), "permission_denied")
        refused(await call(client, "contexture_open", {"ref": fresh}), "permission_denied")
        assert len(authority.calls) > checks_before
        assert system[2].calls == calls_before
        assert [c for c in system[2].calls if c[2] == "handler"] == [("B", "r", "handler")]


@pytest.mark.asyncio
async def test_http_explicit_run_metadata_without_resolver_is_unsupported(endpoint, system):
    url, authority, _ = endpoint
    async with connect(url) as (client, _):
        metadata: tuple[types.RequestParamsMeta, ...] = (
            {RUN_META_KEY: "untrusted-routing-handle"}, {"contexture/session": "unsupported"},
        )
        for meta in metadata:
            refused(await call(client, "contexture_discover", {}, meta=meta), "unsupported_context")
        assert authority.calls == []
        assert system[2].calls == []
        # The transport connection is not an implicit run context.
        assert payload(await call(client, "contexture_discover", {}))["tools"][0]["ref"] == ref()


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["Contexture-Run", "X-Contexture-Run"])
@pytest.mark.parametrize("with_meta", [False, True])
async def test_http_unsupported_header_never_falls_back_to_fresh_request(
    endpoint, system, header, with_meta,
):
    from mcp.shared.exceptions import MCPError

    url, authority, _ = endpoint
    async with connect(url, headers={header: "SECRET-routing-handle"}) as (client, _):
        meta = {RUN_META_KEY: "SECRET-metadata-handle"} if with_meta else None
        result = await call(client, "contexture_discover", {}, meta=meta)
        refused(result, "unsupported_context")
        assert "SECRET" not in str(result)
        for operation in (client.list_prompts, client.list_resources,
                          lambda: client.get_prompt("command"),
                          lambda: client.read_resource("ctxpub:read")):
            with pytest.raises(MCPError, match="unsupported_context") as error:
                await operation()
            assert "SECRET" not in str(error.value)
    assert authority.calls == []
    assert system[2].calls == []


@pytest.mark.asyncio
async def test_http_dns_rebinding_rejected_before_authority(endpoint, system):
    url, authority, _ = endpoint
    async with httpx2.AsyncClient(trust_env=False, timeout=5) as http:
        for headers, status in (
            ({"Host": "attacker.invalid"}, 421),
            ({"Origin": "http://attacker.invalid"}, 403),
        ):
            response = await http.post(url, headers={
                "Accept": "application/json, text/event-stream", **headers,
            }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            assert response.status_code == status
    assert authority.calls == []
    assert system[2].calls == []