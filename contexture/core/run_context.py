"""Shared run-carrier syntax, never identity, authorization or lease resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

RUN_META_KEY = "contexture/run"
RUN_HEADER_NAME = "Contexture-Run"
RUN_CARRIERS_META_KEY = "contexture/run-carriers"
MAX_RUN_HEADER_BYTES = 4096


class RunCarrierError(ValueError):
    def __init__(self, code: Literal["unsupported_context", "schema_invalid", "limit_exceeded"]):
        self.code = code
        super().__init__("Run context carrier refused")


def validate_run_carrier(*, metadata: Mapping[str, object], header_values: Sequence[str],
                         alternate_header: bool, header_enabled: bool,
                         resolver_configured: bool) -> bool:
    """Validate mutually exclusive explicit selectors; return whether one exists."""
    if any(type(key) is not str for key in metadata):
        raise RunCarrierError("schema_invalid")
    if any(key.startswith("contexture/") and key != RUN_META_KEY for key in metadata):
        raise RunCarrierError("unsupported_context")
    if alternate_header or (header_values and not header_enabled):
        raise RunCarrierError("unsupported_context")
    supplied = RUN_META_KEY in metadata or bool(header_values)
    if supplied and not resolver_configured:
        raise RunCarrierError("unsupported_context")
    if header_values:
        if len(header_values) != 1 or RUN_META_KEY in metadata:
            raise RunCarrierError("schema_invalid")
        value = header_values[0]
        if type(value) is not str:
            raise RunCarrierError("schema_invalid")
        if len(value) > MAX_RUN_HEADER_BYTES:
            raise RunCarrierError("limit_exceeded")
        if not value or any(not 0x21 <= ord(char) <= 0x7e for char in value):
            raise RunCarrierError("schema_invalid")
    return supplied
