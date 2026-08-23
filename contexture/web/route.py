"""Explicit HTTP addresses pointing at Tools the Controller tree already owns."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.errors import ModelValidationError


_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class Route:
    """One REST publication pointer; never a Controller or Role member."""

    method: str
    path: str
    invokes: str
    status: int = 200

    def __post_init__(self) -> None:
        method = str(self.method).upper().strip()
        path = str(self.path).strip()
        invokes = str(self.invokes).strip()
        if method not in _METHODS:
            raise ModelValidationError(f"REST method {method!r} is not supported")
        if not path.startswith("/") or "?" in path or "#" in path or "{" in path:
            raise ModelValidationError(
                f"REST path {path!r} must be one fixed absolute path"
            )
        if path != "/" and path.endswith("/"):
            raise ModelValidationError(f"REST path {path!r} must not end in '/'")
        if not invokes:
            raise ModelValidationError("A REST Route must name one Tool ref")
        if not isinstance(self.status, int) or not 100 <= self.status <= 599:
            raise ModelValidationError("A REST Route status must be an HTTP status")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "invokes", invokes)


__all__ = ["Route"]
