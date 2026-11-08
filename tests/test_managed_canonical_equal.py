"""JCS equality optimization is differential-tested against the existing writer."""

from copy import deepcopy
import math
import random
import struct
from unittest.mock import patch

import pytest

from contexture.core.graph.canonical import canonical_equal, canonical_json, freeze_json
from contexture.core.graph.errors import GraphError


VALUES = [None, True, False, 0, 1, -1, 1.0, -0.0, 1e-27, 1e30, 2**53 - 1,
          "", "1", "\U0001f600", "\ue000", "line\n\t", [], [True], [1], {},
          {"x": True}, {"x": 1}, {"a": [1.0, {"b": None}]}]


@pytest.mark.parametrize("left", VALUES)
@pytest.mark.parametrize("right", VALUES)
def test_equality_matches_existing_canonical_bytes(left, right):
    assert canonical_equal(left, right) == (canonical_json(left) == canonical_json(right))


def test_nested_boolean_number_distinction_and_signed_zero():
    assert not canonical_equal({"nested": [True]}, {"nested": [1]})
    assert canonical_equal({"nested": [-0.0, 1.0]}, {"nested": [0, 1]})
    assert canonical_equal({"a": 1, "\U0001f600": 2}, {"\U0001f600": 2, "a": 1})
    assert canonical_equal(freeze_json({"a": [1, 2]}), {"a": (1.0, 2)})


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), 2**53,
    {"x": float("-inf")}, {"bad": "\ud800"}, {1: "not-string"}, b"not-json"])
def test_invalid_inputs_still_fail_even_when_the_same_object_is_compared(invalid):
    with pytest.raises(GraphError):
        canonical_equal(invalid, invalid)
    with pytest.raises(GraphError):
        canonical_equal({}, invalid)
    with pytest.raises(GraphError):
        canonical_equal(invalid, {})


def test_cycle_is_not_hidden_by_identity_shortcuts():
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(GraphError):
        canonical_equal(cyclic, cyclic)


def test_deterministic_nested_and_binary64_differential_without_mutation():
    rng = random.Random(20261106)
    scalars = list(VALUES[:17])
    for _ in range(100):
        number = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(number):
            scalars.append(number)

    def value(depth):
        if depth == 0 or rng.randrange(3) == 0:
            return rng.choice(scalars)
        if rng.randrange(2):
            return [value(depth - 1) for _ in range(rng.randrange(5))]
        return {key: value(depth - 1) for key in rng.sample(["a", "b", "\U0001f600", "\ue000"], rng.randrange(5))}

    for _ in range(200):
        left = value(3)
        right = deepcopy(left) if rng.randrange(2) else value(3)
        before = canonical_json(left), canonical_json(right)
        assert canonical_equal(left, right) == (before[0] == before[1])
        assert (canonical_json(left), canonical_json(right)) == before


def test_comparison_does_not_allocate_encoded_canonical_payloads():
    with patch("contexture.core.graph.canonical.rfc8785.dumps",
               side_effect=AssertionError("Equality must not render JSON")):
        assert canonical_equal({"nested": list(range(100))}, {"nested": tuple(range(100))})
        assert not canonical_equal({"nested": [True]}, {"nested": [1]})
