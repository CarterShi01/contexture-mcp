"""The developer browser is a read-only loopback adapter over Node Catalog."""

from __future__ import annotations

import http.client
import json
import socket
from urllib.parse import quote

import pytest

from contexture.core.errors import ModelValidationError
from contexture.server import compile_application

from .test_introspection import APP


def _request(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_debug_server_exposes_ui_catalog_node_and_etag_without_invocation() -> None:
    debug = compile_application(APP).debug(view="model", detail="definition")

    with debug.running() as address:
        status, headers, body = _request(address.host, address.port, "GET", "/")
        assert status == 200
        assert b"Contexture Node Catalog" in body
        assert "default-src 'none'" in headers["Content-Security-Policy"]
        assert "img-src data:" in headers["Content-Security-Policy"]

        status, _, body = _request(address.host, address.port, "GET", "/app.js")
        assert status == 200
        assert b"JSON.stringify(node)" in body

        status, headers, body = _request(
            address.host, address.port, "GET", "/api/catalog"
        )
        payload = json.loads(body)
        assert status == 200
        assert payload["schema"] == "contexture.node-catalog/v1"
        assert payload["application"]["detail"] == "definition"
        etag = headers["ETag"]

        status, headers, body = _request(
            address.host,
            address.port,
            "GET",
            "/api/catalog",
            headers={"If-None-Match": etag},
        )
        assert status == 304
        assert headers["ETag"] == etag
        assert body == b""

        status, _, body = _request(
            address.host,
            address.port,
            "GET",
            "/api/nodes/" + quote("root/review", safe=""),
        )
        assert status == 200
        assert json.loads(body)["skill"]["authored_instructions"]

        status, _, body = _request(
            address.host, address.port, "POST", "/api/catalog"
        )
        assert status == 405
        assert json.loads(body) == {"error": "Read-only endpoint."}


def test_debug_server_defaults_to_the_complete_application_forest() -> None:
    debug = compile_application(APP).debug()

    assert debug.catalog.roots == ("root", "commands")
    assert debug.catalog.snapshot().application.view.value == "application"


def test_debug_server_rejects_host_rebinding_and_cross_origin_reads() -> None:
    debug = compile_application(APP).debug()

    with debug.running() as address:
        status, _, _ = _request(
            address.host,
            address.port,
            "GET",
            "/api/catalog",
            headers={"Host": "attacker.example"},
        )
        assert status == 400

        authority = f"{address.host}:{address.port}"
        status, _, _ = _request(
            address.host,
            address.port,
            "GET",
            "/api/catalog",
            headers={"Host": authority, "Origin": "http://127.0.0.1:1"},
        )
        assert status == 403


@pytest.mark.skipif(not socket.has_ipv6, reason="platform has no IPv6 support")
def test_debug_server_supports_the_ipv6_loopback_it_advertises() -> None:
    debug = compile_application(APP).debug()

    try:
        with debug.running(host="::1") as address:
            status, _, body = _request(
                address.host, address.port, "GET", "/healthz"
            )
    except ModelValidationError as error:
        pytest.skip(f"IPv6 loopback is unavailable: {error}")

    assert status == 200
    assert json.loads(body) == {"status": "ok"}


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.8", "example.test"])
def test_debug_server_refuses_non_loopback_bindings(host: str) -> None:
    debug = compile_application(APP).debug()

    with pytest.raises(ModelValidationError, match="loopback"):
        with debug.running(host=host):
            pass
