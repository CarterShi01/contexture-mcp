"""Canonical managed reference syntax only: parsing never grants authority."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import re
from uuid import UUID

from .canonical import validate_id
from .errors import GraphError

MAX_REFERENCE_BYTES = 1024
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+\Z", re.ASCII)


def _invalid() -> GraphError:
    return GraphError("schema_invalid", "Invalid canonical managed reference")


def _generation(value: str) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise _invalid()
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError):
        raise _invalid() from None
    if canonical != value:
        raise _invalid()
    return value


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(validate_id(value).encode("utf-8")).decode("ascii").rstrip("=")


def _decode(value: str) -> str:
    if len(value) > 342 or _BASE64URL.fullmatch(value) is None:
        raise _invalid()
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        identity = validate_id(decoded.decode("utf-8", errors="strict"))
    except (ValueError, UnicodeDecodeError):
        raise _invalid() from None
    if _encode(identity) != value:
        raise _invalid()
    return identity


@dataclass(frozen=True, slots=True)
class ManagedReference:
    """Immutable syntax identity, not proof of a lease, visibility or permission."""

    graph_id: str
    generation_uuid: str
    node_id: str

    def __post_init__(self) -> None:
        validate_id(self.graph_id)
        _generation(self.generation_uuid)
        validate_id(self.node_id)

    def __str__(self) -> str:
        return format_reference(self.graph_id, self.generation_uuid, self.node_id)


def format_reference(graph_id: str, generation_uuid: str, node_id: str) -> str:
    """Encode IDs reversibly, preserving all Unicode scalar text byte-for-byte."""
    result = f"ctxg:v1:{_encode(graph_id)}:{_generation(generation_uuid)}:{_encode(node_id)}"
    if len(result) > MAX_REFERENCE_BYTES:
        raise _invalid()
    return result


def parse_reference(value: str) -> ManagedReference:
    """Parse canonical syntax only; runtime admission must separately check staleness."""
    if not isinstance(value, str) or len(value) > MAX_REFERENCE_BYTES or not value.isascii():
        raise _invalid()
    parts = value.split(":")
    if len(parts) != 5 or parts[:2] != ["ctxg", "v1"]:
        raise _invalid()
    _, _, graph, generation, node = parts
    result = ManagedReference(_decode(graph), _generation(generation), _decode(node))
    if str(result) != value:
        raise _invalid()
    return result