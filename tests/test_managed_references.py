"""Managed refs preserve identities and reject alternative encodings, not authorize."""

from dataclasses import FrozenInstanceError
import base64

import pytest

from contexture.core.graph.errors import GraphError
from contexture.core.graph.references import (
    MAX_REFERENCE_BYTES, ManagedReference, format_reference, parse_reference,
)

GENERATION = "12345678-9abc-4def-8123-456789abcdef"


def b64(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


@pytest.mark.parametrize(("graph", "node"), [
    ("g", "n"), ("图😀", "node/a:b\x00"), ("e\u0301", "é"),
    ("😀" * 64, "x" * 256), ("é" * 128, "😀" * 64),
])
def test_canonical_roundtrip(graph, node):
    wire = format_reference(graph, GENERATION, node)
    assert wire == f"ctxg:v1:{b64(graph)}:{GENERATION}:{b64(node)}"
    assert wire.isascii() and len(wire.encode()) <= MAX_REFERENCE_BYTES
    result = parse_reference(wire)
    assert result == ManagedReference(graph, GENERATION, node)
    assert result.graph_id == graph
    assert result.node_id == node
    assert result.generation_uuid == GENERATION
    assert str(result) == wire
    with pytest.raises(FrozenInstanceError):
        result.node_id = "other"
    assert not hasattr(result, "authorized")


@pytest.mark.parametrize("wire", [
    "", "g/n", f"ctxg:v2:Zw:{GENERATION}:bg",
    f"ctxg:v1:Zw=:{GENERATION}:bg", f"ctxg:v1:Zw==:{GENERATION}:bg",
    f"ctxg:v1:Zx:{GENERATION}:bg",  # Nonzero unused pad bits decode to the same g.
    f"ctxg:v1:Zw:{GENERATION}:bh",  # Same alias for node n.
    f"ctxg:v1::{GENERATION}:bg", f"ctxg:v1:Zw:{GENERATION}:",
    f"ctxg:v1:/w:{GENERATION}:bg", f"ctxg:v1:_w:{GENERATION}:bg",
    f"ctxg:v1:7aCA:{GENERATION}:bg", f"ctxg:v1:Z:{GENERATION}:bg",
    f"ctxg:v1:Z w:{GENERATION}:bg", f"ctxg:v1:Zw\n:{GENERATION}:bg",
    f"ctxg:v1:Zw:{GENERATION}:bg:extra", f"ctxg:v1:Zw:{GENERATION}:bg\n",
    f"ctxg:v1:Zw:{GENERATION.upper()}:bg",
    f"ctxg:v1:Zw:{GENERATION.replace('-', '')}:bg",
    f"ctxg:v1:Zw:{{{GENERATION}}}:bg", f"ctxg:v1:Zw:not-a-uuid:bg",
    f"ctxg:v1:图:{GENERATION}:bg", "x" * 1025,
    f"ctxg:v1:{b64('x' * 257)}:{GENERATION}:bg", None, b'ctxg:v1',
])
def test_noncanonical_references_are_rejected(wire):
    with pytest.raises(GraphError) as error:
        parse_reference(wire)
    assert error.value.code == "schema_invalid"
    assert error.value.location == ""
    assert str(error.value) == "Invalid canonical managed reference"


@pytest.mark.parametrize(("graph", "generation", "node"), [
    ("", GENERATION, "n"), ("g", GENERATION, ""),
    ("g", GENERATION, "\ud800"), ("x" * 257, GENERATION, "n"),
    ("g", GENERATION.upper(), "n"), ("g", "bad", "n"),
])
def test_constructor_and_formatter_validate(graph, generation, node):
    with pytest.raises(GraphError):
        ManagedReference(graph, generation, node)
    with pytest.raises(GraphError):
        format_reference(graph, generation, node)


def test_unicode_and_base64_case_are_not_normalized():
    assert format_reference("é", GENERATION, "n") != format_reference("e\u0301", GENERATION, "n")
    first = parse_reference(f"ctxg:v1:Zw:{GENERATION}:bg")
    second = parse_reference(f"ctxg:v1:dw:{GENERATION}:bg")
    assert first.graph_id == "g" and second.graph_id == "w"


def test_syntax_does_not_assert_generation_freshness_or_uuid_version():
    # Even a syntactically valid unknown/nil generation needs host admission.
    nil = "00000000-0000-0000-0000-000000000000"
    result = parse_reference(format_reference("g", nil, "n"))
    assert result.generation_uuid == nil


def test_base64url_unused_bits_are_checked_for_both_tail_lengths():
    # One-byte input leaves four unused bits; two-byte input leaves two.
    for canonical, alias in (("Zw", "Zx"), ("Z2c", "Z2d")):
        accepted = f"ctxg:v1:{canonical}:{GENERATION}:bg"
        assert str(parse_reference(accepted)) == accepted
        with pytest.raises(GraphError):
            parse_reference(f"ctxg:v1:{alias}:{GENERATION}:bg")


def test_all_scalar_id_bytes_including_controls_are_opaque():
    identity = "\x00\x01\t\n\r /:\\?%=#"
    result = parse_reference(format_reference(identity, GENERATION, identity))
    assert result.graph_id == result.node_id == identity
    # The wire still has exactly five segments and carries no raw controls.
    assert len(str(result).split(":")) == 5
    assert not any(character.isspace() for character in str(result))