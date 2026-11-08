"""The optimized integer/ASCII-key profile stays byte-identical to independent JCS."""

import random

import rfc8785

from contexture.core.graph.canonical import canonical_json


def test_integer_ascii_profile_matches_jcs_for_all_valid_unicode_scalars():
    text = "".join(chr(value) for value in range(0x110000) if not 0xD800 <= value <= 0xDFFF)
    value = {"text": text, "integer": 2**53 - 1}
    assert canonical_json(value) == rfc8785.dumps(value)


def test_nested_integer_profile_and_fallbacks_match_jcs():
    rng = random.Random(20261107)
    for _ in range(200):
        value = {"a": [rng.randrange(-(2**53 - 1), 2**53), None, True, False],
                 "z": {"escaped": "\b\f\n\r\t\"\\/<>", "unicode": "\u2028\u2029😀"}}
        assert canonical_json(value) == rfc8785.dumps(value)
    for value in ({"float": -0.0}, {"float": 1e-7}, {"\ue000": 1, "😀": 2}):
        assert canonical_json(value) == rfc8785.dumps(value)
