"""Bounded, transport-independent failures for the managed graph core."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
MAX_DIAGNOSTICS = 32


def _bounded(value: str, limit: int, field: str) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Invalid diagnostic {field}")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"Invalid diagnostic {field}") from None
    return value


def _code(value: str) -> str:
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        raise ValueError("Invalid diagnostic code")
    return value


def _location(value: str) -> str:
    value = _bounded(value, 4096, "location")
    if value and not value.startswith("/"):
        raise ValueError("Diagnostic location must be a JSON pointer")
    if re.search(r"~(?![01])", value):
        raise ValueError("Invalid JSON pointer escape")
    return value


@dataclass(frozen=True, slots=True)
class GraphDiagnostic:
    """A finite diagnostic shape; adapters decide authorization and disclosure."""

    code: str
    location: str = ""
    message: str = ""
    node_id: str | None = None
    publication_id: str | None = None

    def __post_init__(self) -> None:
        _code(self.code)
        _location(self.location)
        _bounded(self.message, 1024, "message")
        for value in (self.node_id, self.publication_id):
            if value is not None:
                _bounded(value, 256, "identity")
                if not 1 <= len(value.encode("utf-8")) <= 256:
                    raise ValueError("Invalid diagnostic identity")


class GraphError(ValueError):
    """Stable code/location plus bounded diagnostics, without adapter state."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        location: str = "",
        diagnostics: Iterable[GraphDiagnostic] = (),
    ) -> None:
        self._code = _code(code)
        self._location = _location(location)
        _bounded(message, 1024, "message")
        items: list[GraphDiagnostic] = []
        for item in diagnostics:
            if len(items) >= MAX_DIAGNOSTICS:
                raise ValueError("Too many graph diagnostics")
            if not isinstance(item, GraphDiagnostic):
                raise TypeError("Expected GraphDiagnostic")
            items.append(item)
        self._diagnostics = tuple(sorted(
            items,
            key=lambda d: (d.location, d.code, d.node_id or "", d.publication_id or ""),
        ))
        super().__init__(message or code)

    @property
    def code(self) -> str:
        return self._code

    @property
    def location(self) -> str:
        return self._location

    @property
    def diagnostics(self) -> tuple[GraphDiagnostic, ...]:
        return self._diagnostics

    def to_dict(self) -> dict[str, object]:
        """Return a detached, finite shape; not an authorization decision."""
        return {
            "code": self.code,
            "location": self.location,
            "message": str(self),
            "diagnostics": [
                {key: value for key, value in {
                    "code": d.code, "location": d.location, "message": d.message,
                    "node_id": d.node_id, "publication_id": d.publication_id,
                }.items() if value is not None}
                for d in self.diagnostics
            ],
        }