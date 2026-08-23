"""The REST surface publishes explicit routes over the shared runtime."""

from __future__ import annotations

import asyncio
import json
import unittest

from contexture import Channels, Contexture, Principal, Role, Tool, current_principal
from contexture.core.errors import ModelValidationError
from contexture.server import compile_application
from contexture.web import RestSurface, Route


class Read(Tool):
    def __init__(self) -> None:
        super().__init__(name="read", description="Read one value.", read_only=True)

    async def invoke(self, name: str = "world") -> dict:
        who = current_principal()
        return {"hello": name, "principal": who.subject if who else None}


class Write(Tool):
    def __init__(self) -> None:
        super().__init__(name="write", description="Write one value.")

    async def invoke(self, value: str) -> dict:
        who = current_principal()
        return {"value": value, "principal": who.subject if who else None}


class Hidden(Tool):
    def __init__(self) -> None:
        super().__init__(name="hidden", description="Not on HTTP.", read_only=True)

    async def invoke(self) -> str:
        return "secret"


class Root(Role):
    def __init__(self) -> None:
        super().__init__(name="root", description="Web fixture.", instructions="Use routes.",
                         tools=[Read(), Write(), Hidden()])


async def request(app, method: str, path: str, *, body=None, headers=None, query: str = ""):
    sent = []
    incoming = [{"type": "http.request", "body": b"" if body is None else json.dumps(body).encode(),
                 "more_body": False}]

    async def receive():
        return incoming.pop(0)

    async def send(message):
        sent.append(message)

    raw_headers = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    if body is not None and not any(key == b"content-type" for key, _ in raw_headers):
        raw_headers.append((b"content-type", b"application/json"))
    await app({"type": "http", "method": method, "path": path,
               "query_string": query.encode(), "headers": raw_headers}, receive, send)
    status = sent[0]["status"]
    payload = json.loads(sent[1]["body"] or b"null")
    return status, payload, dict(sent[0]["headers"])


class RestSurfaceTests(unittest.IsolatedAsyncioTestCase):
    def surface(self, *, authenticate=None):
        runtime = compile_application(Contexture(name="web", roots=(Root,))).runtime()
        return RestSurface(runtime, routes=(
            Route("GET", "/v1/value", "root/read"),
            Route("POST", "/v1/value", "root/write", status=201),
        ), authenticate=authenticate)

    async def test_get_and_post_reach_the_same_controller_runtime(self) -> None:
        async def authenticate(request):
            return Principal(subject=request.headers.get("authorization"))

        app = self.surface(authenticate=authenticate)
        status, value, _ = await request(app, "GET", "/v1/value",
                                         headers={"authorization": "alice"}, query="name=Ada")
        self.assertEqual((status, value), (200, {"hello": "Ada", "principal": "alice"}))
        status, value, _ = await request(app, "POST", "/v1/value", body={"value": "x"},
                                         headers={"authorization": "bob"})
        self.assertEqual((status, value), (201, {"value": "x", "principal": "bob"}))

    async def test_valid_but_unpublished_tool_is_not_http_reachable(self) -> None:
        status, value, headers = await request(self.surface(), "GET", "/root/hidden")
        self.assertEqual(status, 404)
        self.assertEqual(headers[b"content-type"], b"application/problem+json; charset=utf-8")
        self.assertEqual(value["type"], "urn:contexture:problem:route-not-found")

    async def test_a_forged_principal_header_has_no_effect(self) -> None:
        status, value, _ = await request(self.surface(), "GET", "/v1/value",
                                         headers={"x-oc-principal": "founder"})
        self.assertEqual(status, 200)
        self.assertIsNone(value["principal"])

    async def test_authenticator_refusal_is_401(self) -> None:
        status, value, _ = await request(self.surface(authenticate=lambda _request: None),
                                         "GET", "/v1/value")
        self.assertEqual(status, 401)
        self.assertEqual(value["type"], "urn:contexture:problem:unauthenticated")

    async def test_invalid_arguments_are_422(self) -> None:
        status, value, _ = await request(self.surface(), "POST", "/v1/value", body={})
        self.assertEqual(status, 422)
        self.assertEqual(value["type"], "urn:contexture:problem:invalid-arguments")


class RouteValidationTests(unittest.TestCase):
    def runtime(self):
        return compile_application(Contexture(name="web", roots=(Root,))).runtime()

    def test_read_write_mismatch_fails_at_construction(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "requires GET/HEAD"):
            RestSurface(self.runtime(), routes=(Route("POST", "/read", "root/read"),))
        with self.assertRaisesRegex(ModelValidationError, "requires a writing method"):
            RestSurface(self.runtime(), routes=(Route("GET", "/write", "root/write"),))

    def test_missing_target_and_duplicate_route_fail_at_construction(self) -> None:
        with self.assertRaisesRegex(ModelValidationError, "names no Tool"):
            RestSurface(self.runtime(), routes=(Route("GET", "/missing", "root/missing"),))
        with self.assertRaisesRegex(ModelValidationError, "declared twice"):
            RestSurface(self.runtime(), routes=(
                Route("GET", "/same", "root/read"), Route("GET", "/same", "root/read"),
            ))


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_serving_opens_and_closes_application_channels_once(self) -> None:
        marks = []

        class Handle(Channels):
            async def open(self):
                marks.append("open")

            async def close(self):
                marks.append("close")

        runtime = compile_application(
            Contexture(name="web-life", roots=(Root,), channels=Handle)
        ).runtime()
        surface = RestSurface(runtime, routes=(Route("GET", "/v1/value", "root/read"),))
        async with surface.lifespan():
            self.assertEqual(marks, ["open"])
        self.assertEqual(marks, ["open", "close"])


if __name__ == "__main__":
    unittest.main()
