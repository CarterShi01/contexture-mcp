"""Managed JCS profile. Deliberately separate from the legacy Catalog writer."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import TypeAlias, cast

import rfc8785

from .errors import GraphError

JSON: TypeAlias = None | bool | int | float | str | list["JSON"] | dict[str, "JSON"]
FrozenJSON: TypeAlias = (
    None | bool | int | float | str | tuple["FrozenJSON", ...] | Mapping[str, "FrozenJSON"]
)
_JSONLocation: TypeAlias = str | list[str | int]
CANONICAL_PROFILE = "contexture.canonical/jcs-binary64-safeint-v1"
MAX_SAFE_INTEGER = 2**53 - 1
MAX_COUNTER = 10**20 - 1
# Candidate managed contracts only; schema tags are not automatically domains.
JOURNAL_PAYLOAD_DOMAIN = "contexture.journal-payload/v1"
JOURNAL_RECORD_DOMAIN = "contexture.journal-record/v1"
CHECKPOINT_DOMAIN = "contexture.checkpoint/v1"
JOURNAL_BACKUP_DOMAIN = "contexture.file-journal-backup/v1"
DOMAINS = frozenset({
    "contexture.graph/v1", "contexture.facts/v1", "contexture.manifest/v1",
    JOURNAL_RECORD_DOMAIN, "contexture.view/v1", "contexture.page/v1",
    "contexture.command/v1", "contexture.preview/v1",
    JOURNAL_PAYLOAD_DOMAIN, CHECKPOINT_DOMAIN, JOURNAL_BACKUP_DOMAIN,
})
_EMPTY_OBJECT: Mapping[str, FrozenJSON] = MappingProxyType({})


def _invalid(message: str, location: _JSONLocation = "") -> GraphError:
    if not isinstance(location, str):
        pointer = ""
        for key in location:
            pointer = _pointer(pointer, str(key))
        location = pointer
    return GraphError("schema_invalid", message, location=location)


def _scalar(value: str, location: _JSONLocation) -> str:
    if value.isascii():
        return value
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise _invalid("JSON strings must contain Unicode scalars", location) from None
    return value


def _pointer(parent: str, key: str) -> str:
    # Oversized keys remain legal JSON. Report their nearest bounded ancestor
    # rather than imposing a schema key limit or emitting a broken pointer.
    if len(parent) + len(key) + 1 > 4096:
        return parent
    result = parent + "/" + key.replace("~", "~0").replace("/", "~1")
    return result if len(result) <= 4096 else parent


def _visit_json(item: object, location: list[str | int],
                active: set[int], frozen: bool) -> FrozenJSON | JSON:
    if item is None or type(item) is bool:
        return item
    if type(item) is str:
        return _scalar(item, location)
    if type(item) is int:
        if abs(item) > MAX_SAFE_INTEGER:
            raise _invalid("Integer exceeds binary64 safe integer range", location)
        return item
    if type(item) is float:
        if not math.isfinite(item):
            raise _invalid("JSON numbers must be finite", location)
        return item
    item_type = type(item)
    if item is _EMPTY_OBJECT or (item_type is dict and not item):
        return _EMPTY_OBJECT if frozen else {}
    if item_type in (list, tuple) and not item:
        return () if frozen else []
    is_object = item_type in (dict, MappingProxyType) or isinstance(item, Mapping)
    if not is_object and item_type not in (list, tuple) and not isinstance(item, (list, tuple)):
        raise _invalid("Value is not JSON", location)
    identity = id(item)
    if identity in active:
        raise _invalid("Cyclic values are not JSON", location)
    active.add(identity)
    try:
        if is_object:
            item = cast(Mapping[object, object], item)
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise _invalid("JSON object keys must be strings", location)
                _scalar(key, location)
                location.append(key)
                try:
                    result[key] = _visit_json(child, location, active, frozen)
                finally:
                    location.pop()
            return MappingProxyType(result) if frozen else result
        children = []
        for index, child in enumerate(cast(list[object] | tuple[object, ...], item)):
            location.append(index)
            try:
                children.append(_visit_json(child, location, active, frozen))
            finally:
                location.pop()
        if frozen:
            return cast(tuple[FrozenJSON, ...], tuple(children))
        return cast(list[JSON], children)
    finally:
        active.remove(identity)


def _copy_json(value: object, *, frozen: bool) -> FrozenJSON | JSON:
    # Module-level recursion avoids a self-retaining function/cell per copy.
    # Each call owns its path stack; pointers are materialized only on errors.
    if value is _EMPTY_OBJECT or (type(value) is dict and not value):
        return _EMPTY_OBJECT if frozen else {}
    if type(value) in (list, tuple) and not value:
        return () if frozen else []
    try:
        return _visit_json(value, [], set(), frozen)
    except RecursionError:
        raise GraphError("limit_exceeded", "JSON nesting exceeds processing budget") from None


def freeze_json(value: object) -> FrozenJSON:
    """Validate and detach JSON, recursively replacing objects/arrays by read-only forms."""
    return cast(FrozenJSON, _copy_json(value, frozen=True))


def thaw_json(value: object) -> JSON:
    """Return a validated, detached JSON tree of ordinary dicts and lists."""
    return cast(JSON, _copy_json(value, frozen=False))


def canonical_json(value: object) -> bytes:
    """Encode validated JSON using JCS UTF-16 key order and binary64 numbers.

    Raw imports must enter through decode_json: a float has already lost its
    original lexeme and cannot prove whether it originated as a large integer
    or as a legitimate fraction rounded to that same binary64 value.
    """
    normalized = thaw_json(value)
    try:
        pending = [normalized]
        simple = True
        while pending:
            item = pending.pop()
            if isinstance(item, float):
                simple = False
                break
            if isinstance(item, dict):
                if any(not key.isascii() for key in item):
                    simple = False
                    break
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        if simple:
            # ASCII key order and safe integral numbers coincide with JCS.
            # Floats/non-ASCII keys retain the independent RFC8785 encoder.
            return json.dumps(normalized, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")
        return rfc8785.dumps(normalized)
    except rfc8785.CanonicalizationError:
        raise _invalid("Value cannot be represented by the managed JCS profile") from None
    except RecursionError:
        raise GraphError("limit_exceeded", "JSON nesting exceeds processing budget") from None


def canonical_digest(value: object, domain: str) -> str:
    """Hash prefix + NUL + canonical bytes; callers select and normalize the payload."""
    if not isinstance(domain, str) or domain not in DOMAINS:
        raise _invalid("Unsupported managed digest domain")
    return "sha256:" + hashlib.sha256(
        domain.encode("utf-8") + b"\0" + canonical_json(value)
    ).hexdigest()


def _equal_json(a: FrozenJSON, b: FrozenJSON) -> bool:
    if a is b:
        return True
    # Python equates True with 1; JCS keeps booleans distinct from numbers.
    if type(a) is bool or type(b) is bool:
        return type(a) is type(b) and a == b
    if isinstance(a, Mapping):
        return (isinstance(b, Mapping) and a.keys() == b.keys()
                and all(_equal_json(a[key], b[key]) for key in a))
    if isinstance(a, tuple):
        return (isinstance(b, tuple) and len(a) == len(b)
                and all(_equal_json(x, y) for x, y in zip(a, b)))
    return a == b


def canonical_equal(left: object, right: object) -> bool:
    """Validate both JSON trees and compare their JCS meaning without encoding."""
    if left is None and right is None:
        return True
    if type(left) is bool and type(right) is bool:
        return left == right
    if type(left) is str and type(right) is str:
        return _scalar(left, "") == _scalar(right, "")

    try:
        return _equal_json(freeze_json(left), freeze_json(right))
    except RecursionError:
        raise GraphError("limit_exceeded", "JSON nesting exceeds processing budget") from None


def _number(lexeme: str) -> int | float:
    # Decimal is used only for the lexical gate; fractional values are binary64.
    # A zero coefficient stays zero for every exponent, even beyond Decimal's
    # implementation-specific exponent bounds. Preserve the negative-zero sign.
    coefficient = lexeme.lower().split("e", 1)[0]
    if not coefficient.strip("-0."):
        return -0.0 if lexeme.startswith("-") else 0
    try:
        exact = Decimal(lexeme)
    except InvalidOperation:
        raise _invalid("Invalid JSON number") from None
    if not exact.is_finite():
        raise _invalid("JSON numbers must be finite")
    if exact == exact.to_integral_value() and exact.copy_abs() > MAX_SAFE_INTEGER:
        raise _invalid("Integer lexeme exceeds binary64 safe integer range")
    number = float(lexeme)
    if not math.isfinite(number):
        raise _invalid("JSON number overflows binary64")
    if number == 0 and not exact.is_zero():
        raise _invalid("Nonzero JSON number underflows binary64")
    if "." not in lexeme and "e" not in lexeme.lower() and lexeme != "-0":
        return int(number)
    return number


def _pairs(pairs: list[tuple[str, JSON]]) -> dict[str, JSON]:
    result: dict[str, JSON] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("Duplicate JSON object key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise _invalid("Nonfinite JSON constants are forbidden")


def decode_json(data: bytes) -> JSON:
    """Strict UTF-8 raw decoder; numeric lexemes are checked before rounding."""
    if not isinstance(data, bytes):
        raise _invalid("JSON input must be UTF-8 bytes")
    if data.startswith(b"\xef\xbb\xbf"):
        raise _invalid("JSON BOM is forbidden")
    try:
        text = data.decode("utf-8", errors="strict")
        value = json.loads(
            text, parse_int=_number, parse_float=_number,
            parse_constant=_constant, object_pairs_hook=_pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _invalid("Invalid UTF-8 JSON input") from None
    except RecursionError:
        raise GraphError("limit_exceeded", "JSON nesting exceeds processing budget") from None
    return thaw_json(value)


def parse_counter(value: object, *, location: str = "") -> int:
    """Parse a wire decimal string (not a JSON number), capped at 10**20 - 1."""
    if (type(value) is not str or len(value) > 20
            or re.fullmatch(r"0|[1-9][0-9]*", value, flags=re.ASCII) is None):
        raise _invalid("Counter must be a canonical decimal string of at most 20 digits", location)
    return int(value)


def validate_id(value: object, *, location: str = "") -> str:
    """Validate an opaque Unicode-scalar identity without normalization."""
    if type(value) is not str:
        raise _invalid("Identity must be a string", location)
    if len(value) > 256:
        raise _invalid("Identity must contain 1 through 256 UTF-8 bytes", location)
    _scalar(value, location)
    if not 1 <= len(value.encode("utf-8")) <= 256:
        raise _invalid("Identity must contain 1 through 256 UTF-8 bytes", location)
    return value