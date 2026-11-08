"""Real official-SDK HTTP and REST run pins, independent of transport sessions."""

import httpx2
from mcp.server.mcpserver import Context
import pytest

from contexture.application.managed_run_leases import RunLeaseService
from contexture.core.run_context import (
    MAX_RUN_HEADER_BYTES, RUN_CARRIERS_META_KEY, RUN_HEADER_NAME, RUN_META_KEY,
)
from contexture.server.managed import ManagedMCPServer
from contexture.web.managed import ManagedRestSurface
from tests.test_managed_http import call, connect, payload, refused, serve
from tests.test_managed_runtime import binding_switch, ref, setup, swap, system as system
from tests.test_managed_rest import system as rest_system, publish, edit, route

HANDLE = "host-issued-routing-handle"


@pytest.mark.asyncio
async def test_real_mcp_http_explicit_header_and_metadata_pin_same_retained_generation(system):
    coordinator, authority, runtime = await setup(system)
    runs = RunLeaseService(runtime)
    run = await runs.create(ttl=30, selection=frozenset({"r"}))

    class Resolver:
        async def resolve(self, context: Context):
            request = context.request_context.request
            header = request.headers.get(RUN_HEADER_NAME) if hasattr(request, "headers") else None
            value = header if header is not None else (context.request_context.meta or {}).get(RUN_META_KEY)
            return run if value == HANDLE else None

    adapter = ManagedMCPServer(runtime, run_context_resolver=Resolver(), enable_run_header=True)
    try:
        binding_switch(system)
        await swap(system, coordinator)
        async with serve(adapter) as url:
            async with connect(url, headers={RUN_HEADER_NAME: HANDLE}) as (client, _):
                tools = (await client.list_tools()).tools
                assert all(tool.meta[RUN_CARRIERS_META_KEY]["carriers"] == [
                    "mcp-meta:" + RUN_META_KEY, "http-header:" + RUN_HEADER_NAME] for tool in tools)
                assert payload(await call(client, "contexture_discover", {}))["tools"][0]["ref"] == ref()
                assert payload(await call(client, "contexture_invoke_read_only",
                    {"ref": ref(), "arguments": {"count": "6"}}))["version"] == "A"
                authority.scope = frozenset()
                refused(await call(client, "contexture_open", {"ref": ref()}), "permission_denied")
                authority.scope = frozenset({"r", "w", "p", "reserved"})
            async with connect(url) as (client, _):
                assert payload(await call(client, "contexture_discover", {}))["tools"][0]["ref"] == ref(generation=102)
                assert payload(await call(client, "contexture_discover", {},
                    meta={RUN_META_KEY: HANDLE}))["tools"][0]["ref"] == ref()
            runs.release(run)
            async with connect(url, headers={RUN_HEADER_NAME: HANDLE}) as (client, _):
                refused(await call(client, "contexture_open", {"ref": ref()}), "permission_denied")
    finally:
        runs.shutdown()
        await coordinator.shutdown()
    assert [item for item in system[2].calls if item[2] == "handler"] == [("A", "r", "handler")]


@pytest.mark.asyncio
@pytest.mark.parametrize("case,code", [
    ("unknown", "permission_denied"), ("empty", "schema_invalid"),
    ("both", "schema_invalid"), ("duplicate", "schema_invalid"),
    ("oversize", "limit_exceeded"),
])
async def test_real_mcp_header_failures_never_enter_a_fresh_runtime(system, case, code):
    coordinator, authority, runtime = await setup(system)

    class Resolver:
        async def resolve(self, context):
            return None

    headers = httpx2.Headers([(RUN_HEADER_NAME, HANDLE), (RUN_HEADER_NAME, HANDLE)]) if case == "duplicate" else {
        RUN_HEADER_NAME: "" if case == "empty" else
            "x" * (MAX_RUN_HEADER_BYTES + 1) if case == "oversize" else HANDLE}
    adapter = ManagedMCPServer(runtime, run_context_resolver=Resolver(), enable_run_header=True)
    try:
        async with serve(adapter) as url:
            async with connect(url, headers=headers) as (client, _):
                result = await call(client, "contexture_discover", {},
                    meta={RUN_META_KEY: HANDLE} if case == "both" else None)
                refused(result, code)
    finally:
        await coordinator.shutdown()
    assert authority.calls == []
    assert system[2].calls == []


@pytest.mark.asyncio
async def test_real_rest_header_pin_and_accidental_fresh_scope_refuse(rest_system):
    publish(rest_system)
    coordinator, authority, runtime = await setup(rest_system)
    runs = RunLeaseService(runtime)
    run = await runs.create(ttl=30, selection=frozenset({"r"}))

    class Resolver:
        async def resolve(self, request):
            if request.headers.get(RUN_HEADER_NAME) != HANDLE:
                return None
            return runtime.request(run_context=run, publication_audience=True)

    class FreshFallback:
        async def resolve(self, request):
            return runtime.request(publication_audience=True)

    surface = ManagedRestSurface(runtime, run_context_resolver=Resolver(), enable_run_header=True)
    assert surface.supported_run_carriers == ("http-header:" + RUN_HEADER_NAME,)
    try:
        edit(rest_system, [{"op": "put_publication", "publication": route(
            path="/read-new")}], key="route-version-two")
        await swap(rest_system, coordinator)
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=surface),
                                       base_url="http://rest.test") as client:
            retained = await client.get("/read?count=6", headers={RUN_HEADER_NAME: HANDLE})
            assert retained.status_code == 200 and retained.json()["version"] == "A"
            assert retained.headers["Contexture-Generation"] == ref().split(":")[3]
            assert (await client.get("/read?count=7")).status_code == 404
            fresh = await client.get("/read-new?count=7")
            assert fresh.status_code == 200 and fresh.json()["count"] == 7
            assert fresh.headers["Contexture-Generation"] == ref(generation=102).split(":")[3]
            assert (await client.get("/read-new?count=7", headers={RUN_HEADER_NAME: HANDLE})).status_code == 404
            assert (await client.get("/read?count=7",
                headers={RUN_HEADER_NAME: "unknown"})).status_code == 403
            authority.scope = frozenset()
            before_revoke = list(rest_system[2].calls)
            revoked = await client.get("/read?count=6", headers={RUN_HEADER_NAME: HANDLE})
            assert revoked.status_code == 404
            assert "Contexture-Generation" not in revoked.headers
            assert rest_system[2].calls == before_revoke
            authority.scope = frozenset({"r", "w", "p", "reserved"})
        bad = ManagedRestSurface(runtime, run_context_resolver=FreshFallback(), enable_run_header=True)
        before = list(rest_system[2].calls)
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=bad),
                                       base_url="http://rest.test") as client:
            assert (await client.get("/read-new?count=8",
                headers={RUN_HEADER_NAME: HANDLE})).status_code == 403
        assert rest_system[2].calls == before
    finally:
        runs.shutdown()
        await coordinator.shutdown()


def test_header_support_is_explicit_host_configuration():
    with pytest.raises(ValueError):
        ManagedMCPServer(None, enable_run_header=True)
    with pytest.raises(ValueError):
        ManagedRestSurface(None, enable_run_header=True)
