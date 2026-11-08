"""WP09 REST candidate: real graph/compiler/journal and actual ASGI requests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import socket
from unittest.mock import patch

import httpx2 as httpx
import pytest
import uvicorn

from contexture.core.graph.errors import GraphError
from contexture.web.managed import ManagedRestSurface
from tests.test_managed_owner import LATER
from contexture.application.managed_releases import PROJECTION_NAME, release_projection, ReleaseIntentCoordinator
from contexture.server.managed_journal import FileJournal
from tests.test_managed_owner import Host, owner, seed, SCHEMA
from tests.test_managed_runtime import Execution, Provider, definition
from tests.test_managed_runtime import setup, swap


@pytest.fixture
def system(tmp_path):
    class RestExecution(Execution):
        # The shared runtime fixture indexes resources by binding/version and
        # overwrites that slot on unchanged-binding swaps. Track actual handles
        # instead, so old/new generations remain independently observable.
        def __init__(self):
            super().__init__()
            self.seen_handles = []

        def registration(self, node, version="A", **kwargs):
            registration = super().registration(node, version, **kwargs)

            async def handler(decoded, context, resource):
                self.calls.append((version, node, "handler"))
                self.seen_handles.append(resource)
                result = {"version": version, "count": decoded["count"],
                          "actor": context.actor_id, "tenant": context.tenant_id,
                          "nested": decoded.get("nested", [])}
                self.results.append(result)
                return result

            return replace(registration, handler=handler)

    with FileJournal.open(tmp_path / "store", schema=SCHEMA) as journal:
        host, execution = Host(), RestExecution()
        provider = Provider(execution)
        initial = definition(provider)
        contracts = {}
        for n, node in initial.nodes.items():
            assert node.binding is not None
            contracts[n] = provider.registry.resolve(node.binding).publication_contract
        manager = owner(journal, host, publication_contracts=contracts,
                        projections={PROJECTION_NAME: release_projection()})
        envelope = seed()
        envelope["seed"]["inline"] = initial.as_json()
        manager.commit_bootstrap(manager.preview_bootstrap(envelope, expires_at=LATER))
        try:
            yield manager, host, execution, provider, ReleaseIntentCoordinator(manager, manifests=provider)
        finally:
            manager.close()


def route(publication_id="read-stable", method="GET", path="/read", target="r"):
    return {"kind": "route", "publication_id": publication_id, "method": method,
            "path": path, "opens_id": target}


def edit(state, commands, key="rest-routes"):
    manager = state[0]
    envelope = {"schema": "contexture.transaction/v1", "graph_id": "g",
        "base_revision": manager.read("g").revision, "idempotency_key": key,
        "reason": "REST candidate test", "correlation_id": "trace", "commands": commands}
    manager.commit(manager.preview(envelope, expires_at=LATER))


def publish(state):
    entries = [route(), route("head-stable", "HEAD", "/head"),
               route("write-stable", "POST", "/write", "w"),
               route("person-stable", "GET", "/person", "p"),
               route("reserved-stable", "GET", "/reserved", "reserved")]
    edit(state, [{"op": "put_publication", "publication": p} for p in entries])


@asynccontextmanager
async def client_for(state):
    publish(state)
    coordinator, authority, runtime = await setup(state)
    surface = ManagedRestSurface(runtime)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=surface),
                                     base_url="http://rest.test") as client:
            yield client, coordinator, authority, runtime, surface
    finally:
        await coordinator.shutdown()


@pytest.mark.asyncio
async def test_real_starlette_routing_arguments_methods_identity_and_detachment(system):
    async with client_for(system) as (client, _, authority, runtime, surface):
        assert surface.asgi_app() is surface
        with patch.object(runtime, "request", wraps=runtime.request) as requests:
            read = await client.get("/read?count=3")
            write = await client.post("/write", json={"count": 4, "nested": [{"n": 1}]})
            assert requests.call_count == 2
        assert read.status_code == write.status_code == 200
        assert read.json()["count"] == 3 and write.json()["count"] == 4
        assert write.json()["actor"] == system[1].context.actor_id
        assert write.json()["tenant"] == system[1].context.tenant_id
        assert write.headers["cache-control"] == "no-store"
        system[2].results[-1]["nested"][0]["n"] = 99
        assert write.json()["nested"] == [{"n": 1}]
        assert [c.operation for c in authority.calls] == [
            "request", "list_routes", "invoke_read_only", "request", "list_routes", "invoke"]
        for path in ("/person", "/reserved"):
            assert (await client.get(path)).status_code == 200
        assert (await client.head("/read")).status_code == 404
        before = len(system[2].calls)
        head = await client.head("/head?count=5")
        assert head.status_code == 200 and head.content == b""
        assert int(head.headers["content-length"]) > 0
        assert system[2].calls[before:] == [("A", "r", step)
                                           for step in ("decoder", "validator", "handler")]
        for method, path in [("GET", "/write"), ("POST", "/read"), ("OPTIONS", "/read"),
                             ("GET", "/read/"), ("GET", "/r"), ("GET", "/management"),
                             ("GET", "/read-stable"), ("GET", "/")]:
            assert (await client.request(method, path)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"[1]", b"null", b"{", b'{"count":NaN}',
                                    b'{"count":1,"count":2}', b'{"count":9007199254740993}'])
async def test_malformed_body_fails_before_execution(system, body):
    async with client_for(system) as (client, *_):
        response = await client.post("/write", content=body)
        assert response.status_code == 400
        assert system[2].calls == []


@pytest.mark.asyncio
async def test_candidate_parser_and_forged_authority_fields_refuse(system):
    async with client_for(system) as (client, *_):
        cases = [("GET", "/read", {"content": b"{}"}, 400),
                 ("POST", "/write?count=3", {"json": {}}, 400),
                 ("POST", "/write", {"headers": {"content-type": "text/plain"}}, 415),
                 ("GET", "/read?count=1&count=2", {}, 403),
                 ("GET", "/read?arguments=%7B%7D", {}, 403),
                 ("GET", "/read", {"headers": {"X-Contexture-Run": "fake-secret"}}, 400)]
        for method, path, kwargs, expected in cases:
            assert (await client.request(method, path, **kwargs)).status_code == expected
        for key in ("actor_id", "tenant_id", "selection", "authority_revision", "run_context"):
            response = await client.post("/write", json={key: "forged"})
            assert response.status_code == 403 and "forged" not in response.text
        assert system[2].calls == []
        assert (await client.post("/write")).json()["count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["Contexture-Run", "X-Contexture-Run"])
async def test_unsupported_run_carrier_refuses_before_authority(system, header):
    async with client_for(system) as (client, _, authority, _, _):
        response = await client.get("/read", headers={header: "SECRET-routing"})
        assert response.status_code == 400
        assert response.json() == {"error": "run-header-unsupported"}
        assert authority.calls == [] and system[2].calls == []


@pytest.mark.asyncio
async def test_incremental_stream_body_and_response_limits(system):
    async with client_for(system) as (client, _, _, _, _):
        pulled = []

        async def chunks():
            for value in (b'{"count":', b'"123456"', b'}'):
                pulled.append(value)
                yield value

        with patch("contexture.web.managed.MAX_BODY_BYTES", 10):
            response = await client.post("/write", content=chunks())
        assert response.status_code == 413 and len(pulled) == 2
        assert system[2].calls == []
        with patch("contexture.web.managed.MAX_RESPONSE_BYTES", 8):
            response = await client.get("/read")
        assert response.status_code == 500 and response.json() == {"error": "response-too-large"}
        assert "version" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["request", "list_routes", "invoke_read_only"])
async def test_swap_during_async_authorization_keeps_one_pin_and_stable_id(system, stage):
    async with client_for(system) as (client, coordinator, authority, runtime, _):
        authority.block_operation = stage
        old_handle = system[2].resources[("A", "r")]

        async def update():
            await authority.entered.wait()
            try:
                edit(system, [{"op": "put_publication", "publication":
                    route(path="/new", target="p")}], "new-path-and-binding")
                await swap(system, coordinator)
            finally:
                authority.proceed.set()

        task = asyncio.create_task(update())
        try:
            with patch.object(runtime, "request", wraps=runtime.request) as requests:
                result = await asyncio.wait_for(client.get("/read?count=6"), 10)
                assert requests.call_count == 1
            await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert result.status_code == 200 and result.json()["version"] == "A"
        assert system[2].seen_handles[0] is old_handle
        assert system[2].resources[("A", "r")] is not old_handle
        assert next(p.path for p in authority.calls[-1].current_index.definition.publications
                if p.publication_id == "read-stable") == "/new"
        assert [c for c in system[2].calls if c[2] == "handler"] == [("A", "r", "handler")]
        assert (await client.get("/read")).status_code == 404
        assert (await client.get("/new?count=6")).status_code == 200
        assert system[2].calls[-1] == ("A", "p", "handler")
        async with runtime.request(publication_audience=True) as request:
            entries = await request.list_routes()
            assert next(p.path for p in entries if p.publication_id == "read-stable") == "/new"
            result_value = await request.invoke_route("read-stable", {"count": 7},
                expected_method="GET", expected_path="/new")
            assert isinstance(result_value, dict) and result_value["version"] == "A"
    # Retired generations are coordinator-owned; shutdown drains both pins.
    assert len(system[2].closed) == 8


@pytest.mark.asyncio
async def test_fresh_request_after_path_rename_and_public_port_fences(system):
    async with client_for(system) as (client, coordinator, _, runtime, _):
        edit(system, [{"op": "put_publication", "publication": route(path="/renamed")}], "rename")
        await swap(system, coordinator)
        assert (await client.get("/read")).status_code == 404
        assert (await client.get("/renamed")).status_code == 200
        async with runtime.request(publication_audience=True) as request:
            for publication_id, method, path in [("read-stable", "GET", "/read"),
                ("read-stable", "POST", "/renamed"), ("/renamed", "GET", "/renamed")]:
                with pytest.raises(GraphError):
                    await request.invoke_route(publication_id, {},
                        expected_method=method, expected_path=path)
        with pytest.raises(GraphError):
            await request.list_routes()


@pytest.mark.asyncio
async def test_public_port_selection_ceiling_and_cancellation(system):
    async with client_for(system) as (client, _, authority, runtime, _):
        async with runtime.request(selection=frozenset({"r"}),
                                   publication_audience=True) as request:
            assert {p.opens_id for p in await request.list_routes()} == {"r"}
            with pytest.raises(GraphError):
                await request.invoke_route("write-stable", {},
                    expected_method="POST", expected_path="/write")
        authority.allowed = False
        response = await client.get("/read")
        assert response.status_code == 403 and response.json() == {"error": "forbidden"}
        authority.allowed = True

        async def cancel(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch.object(authority, "verify", cancel):
            with pytest.raises(asyncio.CancelledError):
                await client.get("/read")
        assert system[2].calls == []
        assert (await client.get("/read")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "identity", "revision"])
async def test_current_recheck_between_mapping_and_action(system, change):
    async with client_for(system) as (client, _, authority, _, _):
        authority.block_operation = "invoke_read_only"

        async def mutate():
            await authority.entered.wait()
            if change == "revoke":
                authority.scope = frozenset()
            elif change == "identity":
                system[1].context = replace(system[1].context, actor_id="different")
            else:
                system[1].context = replace(system[1].context, authority_revision="2")
            authority.proceed.set()

        task = asyncio.create_task(mutate())
        try:
            response = await asyncio.wait_for(client.get("/read"), 10)
            await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert response.status_code == 403 and response.json() == {"error": "forbidden"}
        assert system[2].calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["graph", "handler", "stale", "denied"])
async def test_sanitized_errors_no_logs(system, failure, caplog):
    async with client_for(system) as (client, _, authority, runtime, _):
        async def fail(*args, **kwargs):
            if failure == "graph":
                raise GraphError("permission_denied", "SECRET-policy", location="/SECRET")
            if failure == "stale":
                raise GraphError("stale_reference", "SECRET-alias")
            if failure == "denied":
                raise PermissionError("SECRET-denial")
            raise RuntimeError("SECRET-handler")

        if failure == "handler":
            with patch("contexture.application.managed_runtime.PythonManagedCallAdapter.call", fail):
                response = await client.get("/read")
        else:
            with patch.object(authority, "verify", fail):
                response = await client.get("/read")
        assert response.status_code == (500 if failure == "handler" else 403)
        assert "SECRET" not in response.text and "SECRET" not in caplog.text
        assert "Traceback" not in caplog.text
        assert runtime is not None


@pytest.mark.parametrize("method,target", [("GET", "w"), ("HEAD", "w"), ("POST", "r")])
def test_graph_index_refuses_wrong_effect_before_serving(system, method, target):
    with pytest.raises(GraphError, match="effect mismatch"):
        edit(system, [{"op": "put_publication", "publication": route(method=method, target=target)}])
    assert system[2].calls == []


@pytest.mark.asyncio
async def test_loopback_real_http_cleanup_and_runtime_owned_resources(system):
    baseline = asyncio.all_tasks()
    async with client_for(system) as (_, _, _, _, surface):
        class ReadyServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                ready.set()

        ready = asyncio.Event()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.setblocking(False)
            port = listener.getsockname()[1]
            server = ReadyServer(uvicorn.Config(surface, host="127.0.0.1", port=port,
                loop="asyncio", lifespan="on", access_log=False, log_level="warning",
                timeout_graceful_shutdown=2))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            waiter = asyncio.create_task(ready.wait())
            try:
                done, _ = await asyncio.wait({task, waiter}, timeout=10,
                                             return_when=asyncio.FIRST_COMPLETED)
                assert waiter in done and server.started
                async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                    response = await client.get(f"http://127.0.0.1:{port}/read?count=9")
                    assert response.status_code == 200 and response.json()["count"] == 9
                    assert (await client.head(f"http://127.0.0.1:{port}/head")).content == b""
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                server.should_exit = True
                try:
                    await asyncio.wait_for(asyncio.shield(task), 5)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert not server.server_state.tasks and not server.server_state.connections
            assert all(not bound.is_serving() for bound in server.servers)
            assert system[2].closed == []  # HTTP shutdown does not own runtime.
        with pytest.raises(OSError):
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
            writer.close()
            await writer.wait_closed()
    assert len(system[2].closed) == 4
    assert not {t for t in asyncio.all_tasks() - baseline if not t.done()}