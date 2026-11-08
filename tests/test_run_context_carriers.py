"""Carrier syntax is not authorization and never permits a fresh fallback."""

import pytest

from contexture.core.run_context import (
    MAX_RUN_HEADER_BYTES, RUN_META_KEY, RunCarrierError, validate_run_carrier,
)


def validate(*, metadata=None, headers=(), enabled=True, resolver=True, alternate=False):
    return validate_run_carrier(metadata={} if metadata is None else metadata,
        header_values=headers, alternate_header=alternate, header_enabled=enabled,
        resolver_configured=resolver)


def test_explicit_independent_carriers_and_ordinary_request():
    assert not validate()
    assert validate(metadata={RUN_META_KEY: "opaque-handle"})
    assert validate(headers=("opaque-handle",))
    assert validate(headers=("x" * MAX_RUN_HEADER_BYTES,))
    assert not validate(metadata={"progressToken": 1})


@pytest.mark.parametrize("kwargs,code", [
    ({"headers": ("handle",), "enabled": False}, "unsupported_context"),
    ({"headers": ("handle",), "resolver": False}, "unsupported_context"),
    ({"metadata": {RUN_META_KEY: "handle"}, "resolver": False}, "unsupported_context"),
    ({"metadata": {"contexture/session": "handle"}}, "unsupported_context"),
    ({"alternate": True}, "unsupported_context"),
    ({"headers": ("handle", "handle")}, "schema_invalid"),
    ({"headers": ("handle",), "metadata": {RUN_META_KEY: "handle"}}, "schema_invalid"),
    ({"headers": ("",)}, "schema_invalid"),
    ({"headers": ("has space",)}, "schema_invalid"),
    ({"headers": ("\u00a0",)}, "schema_invalid"),
    ({"headers": ("has\nnewline",)}, "schema_invalid"),
    ({"headers": ("x" * (MAX_RUN_HEADER_BYTES + 1),)}, "limit_exceeded"),
])
def test_supplied_invalid_selector_is_refused_and_redacted(kwargs, code):
    with pytest.raises(RunCarrierError) as failure:
        validate(**kwargs)
    assert failure.value.code == code
    assert "handle" not in str(failure.value)
