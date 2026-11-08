"""Pure immutable page slicing; no graph, cursor, or authorization authority."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import GraphError


@dataclass(frozen=True, slots=True)
class PageSlice:
    items: tuple[bytes, ...]
    next_offset: int | None


def slice_items(items: tuple[bytes, ...], *, offset: int, limit: int,
                byte_limit: int) -> PageSlice:
    """Slice already authorized JSON cards, never split or omit a large card.

    byte_limit budgets the encoded JSON array, including delimiters. Application
    code must separately budget the COMPLETE response envelope before returning.
    """
    if (type(offset) is not int or not 0 <= offset <= len(items)
            or type(limit) is not int or not 1 <= limit <= 1000
            or type(byte_limit) is not int or byte_limit < 2):
        raise GraphError("schema_invalid", "Invalid native page bounds")
    end, size = offset, 2
    while end < min(len(items), offset + limit):
        added = len(items[end]) + (end > offset)
        if size + added > byte_limit:
            if end == offset:
                raise GraphError("too_large", "One page item exceeds the byte budget")
            break
        size += added
        end += 1
    return PageSlice(items[offset:end], end if end < len(items) else None)