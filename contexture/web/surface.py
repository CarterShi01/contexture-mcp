"""A small ASGI REST adapter over :class:`ApplicationRuntime`."""

from __future__ import annotations

import inspect
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from urllib.parse import parse_qs

from ..core.errors import ModelValidationError, NodeNotFoundError, WrongDoorError
from ..core.model.runtime import ApplicationRuntime
from ..core.principal import Principal
from .route import Route


Authenticator = Callable[["WebRequest"], Principal | None | Awaitable[Principal | None]]


@dataclass(frozen=True, slots=True)
class WebRequest:
    """The protocol facts an authenticator may inspect; no Controller ref."""

    method: str
    path: str
    headers: Mapping[str, str]
    query: Mapping[str, tuple[str, ...]]


class RestSurface:
    """Publish an explicit subset of one Controller Runtime as REST."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        *,
        routes: Sequence[Route | type[Route]],
        authenticate: Authenticator | None = None,
        max_body_bytes: int = 1024 * 1024,
    ) -> None:
        if not isinstance(runtime, ApplicationRuntime):
            raise TypeError("RestSurface needs an ApplicationRuntime")
        if not isinstance(max_body_bytes, int) or max_body_bytes < 1:
            raise ValueError("max_body_bytes must be a positive integer")
        entries = tuple(_route(entry) for entry in routes)
        table: dict[tuple[str, str], Route] = {}
        for entry in entries:
            key = (entry.method, entry.path)
            if key in table:
                raise ModelValidationError(
                    f"REST route {entry.method} {entry.path} is declared twice"
                )
            try:
                tool = runtime.index.tool(entry.invokes)
            except NodeNotFoundError as failure:
                raise ModelValidationError(
                    f"REST route {entry.method} {entry.path} names no Tool: "
                    f"{entry.invokes!r} ({failure.reason.value})"
                ) from None
            request_is_read = entry.method in {"GET", "HEAD"}
            if tool.read_only is not request_is_read:
                expected = "GET/HEAD" if tool.read_only else "a writing method"
                raise ModelValidationError(
                    f"REST route {entry.method} {entry.path} names "
                    f"{entry.invokes!r}; that Tool requires {expected}"
                )
            table[key] = entry
        self.runtime = runtime
        self.routes = entries
        self.authenticate = authenticate
        self.max_body_bytes = max_body_bytes
        self._table = table

    def asgi_app(self) -> "RestSurface":
        """Return this mountable ASGI application."""

        return self

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator["RestSurface"]:
        """Open the compiled application's Channels for a serving lifetime."""

        async with self.runtime.index.provisioned():
            yield self

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") == "lifespan":
            await self._serve_lifespan(receive, send)
            return
        if scope.get("type") != "http":
            raise RuntimeError("RestSurface supports ASGI http and lifespan scopes")
        await self._serve_http(scope, receive, send)

    async def _serve_lifespan(self, receive: Any, send: Any) -> None:
        message = await receive()
        if message.get("type") != "lifespan.startup":
            return
        try:
            async with self.lifespan():
                await send({"type": "lifespan.startup.complete"})
                while True:
                    message = await receive()
                    if message.get("type") == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
        except Exception as error:
            await send({"type": "lifespan.startup.failed", "message": str(error)})

    async def _serve_http(self, scope: dict, receive: Any, send: Any) -> None:
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "/")
        entry = self._table.get((method, path))
        if entry is None and method == "HEAD":
            entry = self._table.get(("GET", path))
        if entry is None:
            await _problem(send, 404, "route-not-found", "No REST route is published here.")
            return
        query = {key: tuple(values) for key, values in parse_qs(
            bytes(scope.get("query_string") or b"").decode("utf-8"),
            keep_blank_values=True,
        ).items()}
        headers = {
            bytes(key).decode("latin-1").lower(): bytes(value).decode("latin-1")
            for key, value in scope.get("headers") or ()
        }
        request = WebRequest(method=method, path=path, headers=headers, query=query)
        principal: Principal | None = None
        if self.authenticate is not None:
            value = self.authenticate(request)
            principal = await value if inspect.isawaitable(value) else value
            if principal is None:
                await _problem(send, 401, "unauthenticated", "Authentication is required.")
                return
            if not isinstance(principal, Principal):
                await _problem(
                    send, 500, "invalid-authenticator",
                    "Authenticator returned an invalid identity.",
                )
                return
        try:
            arguments = await self._arguments(entry, query, headers, receive)
            if entry.method in {"GET", "HEAD"}:
                result = await self.runtime.invoke_read_only(
                    entry.invokes, arguments, principal=principal, context=request
                )
            else:
                result = await self.runtime.invoke(
                    entry.invokes, arguments, principal=principal, context=request
                )
        except _RequestError as error:
            await _problem(send, error.status, error.kind, error.detail)
            return
        except PermissionError as error:
            await _problem(send, 403, "forbidden", str(error) or "Forbidden.")
            return
        # Route construction makes these two failures unreachable at request time.
        except (NodeNotFoundError, WrongDoorError) as error:
            await _problem(send, 500, "invalid-surface", str(error))
            return
        except Exception as error:
            # The production TypeHintBinding preserves the MCP SDK's historical
            # ToolError contract.  This adapter does not import that SDK; it
            # unwraps only by the boundary exception's name and maps the
            # original capability/validation failure for an HTTP audience.
            failure = (
                error.__cause__
                if type(error).__name__ == "ToolError" and error.__cause__
                else error
            )
            if type(failure).__name__ in {"ValidationError", "McpError"}:
                await _problem(send, 422, "invalid-arguments", str(failure))
            elif isinstance(failure, PermissionError):
                await _problem(send, 403, "forbidden", str(failure) or "Forbidden.")
            elif isinstance(failure, ValueError):
                await _problem(send, 422, "rejected", str(failure))
            else:
                await _problem(send, 500, "controller-failed", type(failure).__name__)
            return
        await _json_response(send, entry.status, result, head=(method == "HEAD"))

    async def _arguments(
        self, entry: Route, query: Mapping[str, tuple[str, ...]],
        headers: Mapping[str, str], receive: Any,
    ) -> dict[str, Any]:
        if entry.method in {"GET", "HEAD"}:
            return {key: values[0] if len(values) == 1 else list(values)
                    for key, values in query.items()}
        content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
        if content_type and content_type != "application/json":
            raise _RequestError(
                415, "unsupported-media-type",
                "REST commands accept application/json.",
            )
        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            body.extend(message.get("body") or b"")
            if len(body) > self.max_body_bytes:
                raise _RequestError(
                    413, "body-too-large",
                    "Request body exceeds the configured limit.",
                )
            if not message.get("more_body"):
                break
        if not body:
            return {}
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _RequestError(400, "invalid-json", "Request body is not valid JSON.") from error
        if not isinstance(value, dict):
            raise _RequestError(400, "invalid-body", "Request body must be a JSON object.")
        return value


class _RequestError(Exception):
    def __init__(self, status: int, kind: str, detail: str) -> None:
        self.status, self.kind, self.detail = status, kind, detail
        super().__init__(detail)


def _route(entry: Route | type[Route]) -> Route:
    value = entry() if isinstance(entry, type) and issubclass(entry, Route) else entry
    if not isinstance(value, Route):
        raise TypeError(f"{entry!r} is not a Route")
    return value


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


async def _json_response(send: Any, status: int, value: Any, *, head: bool = False) -> None:
    body = json.dumps(
        _jsonable(value), ensure_ascii=False, separators=(",", ":"), default=str
    ).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"cache-control", b"no-store"),
        (b"content-length", str(len(body)).encode()),
    ]})
    await send({"type": "http.response.body", "body": b"" if head else body})


async def _problem(send: Any, status: int, kind: str, detail: str) -> None:
    body = json.dumps({"type": f"urn:contexture:problem:{kind}", "status": status,
                       "title": kind.replace("-", " "), "detail": detail},
                      ensure_ascii=False, separators=(",", ":")).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/problem+json; charset=utf-8"),
        (b"cache-control", b"no-store"),
        (b"content-length", str(len(body)).encode()),
    ]})
    await send({"type": "http.response.body", "body": body})


__all__ = ["Authenticator", "RestSurface", "WebRequest"]
