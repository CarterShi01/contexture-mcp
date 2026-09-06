"""Request adapters for root-level Contexture capability projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from ..core.principal import Principal
from ..core.model.disclosure import Disclosure
from ..core.model.index import Index
from ..core.model.root_selection import (
    RootSelection,
    RootSelectionError,
    bound_root_selection,
)
from . import instructions as instructions_module
from .identity import principal_of
from .messages import GOTO_ARGUMENT, GOTO_PROMPT

ROOTS_HEADER = "Contexture-Roots"
RootCeiling = Callable[[Principal | None], RootSelection]


@runtime_checkable
class RootSelector(Protocol):
    """Turn request facts into a resolved, immutable root selection."""

    def select(
        self,
        index: Index,
        headers: Mapping[str, str] | None,
        principal: Principal | None,
    ) -> RootSelection: ...


@dataclass(frozen=True, slots=True)
class HeaderRootSelector:
    """Read an exact comma-separated root allowlist from an HTTP header.

    The header is an attenuation request, never an identity assertion.  An
    optional application-owned ``ceiling`` derives authority from the verified
    Principal; the effective surface is always their intersection.
    """

    header: str = ROOTS_HEADER
    ceiling: RootCeiling | None = None
    max_length: int = 4096
    max_roots: int = 128

    def select(
        self,
        index: Index,
        headers: Mapping[str, str] | None,
        principal: Principal | None,
    ) -> RootSelection:
        raw = _header(headers, self.header)
        if raw is None:
            requested = RootSelection.all()
        else:
            if len(raw) > self.max_length:
                raise RootSelectionError(
                    f"{self.header} exceeds the {self.max_length}-character limit."
                )
            parts = tuple(part.strip() for part in raw.split(","))
            if len(parts) > self.max_roots:
                raise RootSelectionError(
                    f"{self.header} exceeds the {self.max_roots}-root limit."
                )
            requested = RootSelection.only(parts).resolve(index)

        if self.ceiling is None:
            return requested.resolve(index)
        ceiling = self.ceiling(principal)
        if not isinstance(ceiling, RootSelection):
            raise TypeError("A root ceiling must return RootSelection.")
        return requested.intersect(ceiling.resolve(index)).resolve(index)


@dataclass(frozen=True, slots=True)
class FixedRootSelector:
    """A transport-independent fixed surface, useful for stdio hosts."""

    selection: RootSelection

    def select(
        self,
        index: Index,
        headers: Mapping[str, str] | None,
        principal: Principal | None,
    ) -> RootSelection:
        del headers, principal
        return self.selection.resolve(index)


@dataclass(frozen=True, slots=True)
class RootSelectionMiddleware:
    """Bind and consistently enforce one selection across every MCP door."""

    selector: RootSelector
    index: Index
    tree: Disclosure
    prompt_refs: Mapping[str, str]
    resource_refs: Mapping[str, str]
    dynamic_instructions: bool = True

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        try:
            selection = self.selector.select(
                self.index,
                _request_headers(ctx.request),
                principal_of(get_access_token()),
            )
        except RootSelectionError as exc:
            raise MCPError(code=INVALID_PARAMS, message=str(exc)) from exc

        with bound_root_selection(selection):
            self._guard(ctx, selection)
            result = await call_next(ctx)
            return self._filter(ctx.method, result, selection)

    def _guard(self, ctx: ServerRequestContext[Any, Any], selection: RootSelection) -> None:
        params = ctx.params or {}
        ref: str | None = None
        if ctx.method == "prompts/get":
            name = str(params.get("name", ""))
            ref = self.prompt_refs.get(name)
            if name == GOTO_PROMPT:
                arguments = params.get("arguments") or {}
                candidate = arguments.get(GOTO_ARGUMENT)
                ref = str(candidate) if candidate is not None else None
        elif ctx.method == "resources/read":
            ref = self.resource_refs.get(str(params.get("uri", "")))
        if ref is not None and not selection.contains_ref(ref):
            raise MCPError(
                code=INVALID_PARAMS,
                message="The requested entry is outside this request's root surface.",
            )

    def _filter(
        self, method: str, result: HandlerResult, selection: RootSelection
    ) -> HandlerResult:
        if result is None:
            return result

        if method in {"initialize", "server/discover"} and self.dynamic_instructions:
            rendered = instructions_module.build(self.tree.select(selection))
            if isinstance(result, dict):
                return {
                    **result,
                    "instructions": rendered,
                    **({"cacheScope": "private"} if method == "server/discover" else {}),
                }
            updates: dict[str, Any] = {"instructions": rendered}
            if method == "server/discover":
                updates["cache_scope"] = "private"
            return result.model_copy(update=updates)

        if isinstance(result, dict):
            if method == "prompts/list":
                prompts = [
                    prompt
                    for prompt in result.get("prompts", [])
                    if _item(prompt, "name") == GOTO_PROMPT
                    or (
                        _item(prompt, "name") in self.prompt_refs
                        and selection.contains_ref(
                            self.prompt_refs[_item(prompt, "name")]
                        )
                    )
                ]
                return {**result, "prompts": prompts, "cacheScope": "private"}
            if method == "resources/list":
                resources = [
                    resource
                    for resource in result.get("resources", [])
                    if str(_item(resource, "uri")) in self.resource_refs
                    and selection.contains_ref(
                        self.resource_refs[str(_item(resource, "uri"))]
                    )
                ]
                return {**result, "resources": resources, "cacheScope": "private"}
            return result

        if method == "prompts/list" and hasattr(result, "prompts"):
            prompts = [
                prompt
                for prompt in result.prompts
                if prompt.name == GOTO_PROMPT
                or (
                    prompt.name in self.prompt_refs
                    and selection.contains_ref(self.prompt_refs[prompt.name])
                )
            ]
            return result.model_copy(update={"prompts": prompts, "cache_scope": "private"})

        if method == "resources/list" and hasattr(result, "resources"):
            resources = [
                resource
                for resource in result.resources
                if str(resource.uri) in self.resource_refs
                and selection.contains_ref(self.resource_refs[str(resource.uri)])
            ]
            return result.model_copy(
                update={"resources": resources, "cache_scope": "private"}
            )
        return result


def _header(headers: Mapping[str, str] | None, wanted: str) -> str | None:
    if headers is None:
        return None
    direct = headers.get(wanted)
    if direct is not None:
        return direct
    lowered = wanted.lower()
    for name, value in headers.items():
        if name.lower() == lowered:
            return value
    return None


def _request_headers(request: Any) -> Mapping[str, str] | None:
    headers = getattr(request, "headers", None)
    return headers if isinstance(headers, Mapping) else None


def _item(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


__all__ = [
    "FixedRootSelector",
    "HeaderRootSelector",
    "ROOTS_HEADER",
    "RootCeiling",
    "RootSelector",
    "RootSelectionMiddleware",
]
