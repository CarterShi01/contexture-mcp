"""Raw-input and immutable-state evidence for the additive managed profile."""

from decimal import localcontext
import hashlib
import math
from types import MappingProxyType

import pytest

from contexture.core.graph.canonical import (
    CANONICAL_PROFILE, DOMAINS, MAX_COUNTER, canonical_digest, canonical_json, decode_json,
    freeze_json, parse_counter, thaw_json, validate_id,
)
from contexture.core.graph.errors import GraphDiagnostic, GraphError


def test_digest_registry_is_exact_and_shared_with_journal():
    from contexture.server.managed_journal import (
        CHECKPOINT_DOMAIN, JOURNAL_BACKUP_DOMAIN, PAYLOAD_DOMAIN, RECORD_DOMAIN, _digest,
    )

    assert DOMAINS == frozenset({
        "contexture.graph/v1", "contexture.facts/v1", "contexture.manifest/v1",
        "contexture.journal-record/v1", "contexture.view/v1", "contexture.page/v1",
        "contexture.command/v1", "contexture.preview/v1",
        "contexture.journal-payload/v1", "contexture.checkpoint/v1",
        "contexture.file-journal-backup/v1",
    })
    for domain in DOMAINS:
        expected = "sha256:" + hashlib.sha256(domain.encode() + b"\0{}").hexdigest()
        assert canonical_digest({}, domain) == expected
    for domain in (CHECKPOINT_DOMAIN, JOURNAL_BACKUP_DOMAIN, PAYLOAD_DOMAIN, RECORD_DOMAIN):
        assert _digest(domain, {}) == canonical_digest({}, domain)
    for domain in ("contexture.arbitrary/v1", "contexture.graph/v2",
                   "contexture.transaction/v1", "contexture.authorized-batch/v1"):
        with pytest.raises(GraphError):
            canonical_digest({}, domain)
        with pytest.raises(GraphError):
            _digest(domain, {})


@pytest.mark.parametrize("raw", [
    b'{"a":1,"a":2}', b'{"a":1,"\\u0061":2}', b'{"x":{"a":0,"a":1}}',
    b'"\\ud800"', b'"\\udfff"', b'{"\\ud800":0}', b'"\xff"',
    b'"\xed\xa0\x80"', b'\xef\xbb\xbf{}', b'NaN', b'Infinity', b'-Infinity',
    b'9007199254740992', b'-9007199254740993', b'1e20', b'-1E20',
    b'9007199254740993.0', b'9.007199254740992e15', b'1e9999',
    b'1e-9999', b'-1e-9999', b'2e-324', b'1e-999999999999999999999',
    b'{', b'{}{}', b'01', b'1.', b'"line\nfeed"',
])
def test_strict_raw_rejection(raw):
    with pytest.raises(GraphError) as error:
        decode_json(raw)
    assert error.value.code == "schema_invalid"
    assert error.value.location == ""
    assert raw.decode("utf-8", errors="replace") not in str(error.value)


@pytest.mark.parametrize(("raw", "expected"), [
    (b'9007199254740991', b'9007199254740991'),
    (b'-9007199254740991', b'-9007199254740991'),
    (b'0.1', b'0.1'), (b'-0.0', b'0'), (b'-0', b'0'),
    (b'0e-99999', b'0'), (b'5e-324', b'5e-324'),
    (b'333333333.33333329', b'333333333.3333333'),
    (b'9007199254740991.9', b'9007199254740992'),
    (b'0.000001', b'0.000001'), (b'0.0000001', b'1e-7'),
])
def test_binary64_lexical_acceptance_and_jcs(raw, expected):
    assert canonical_json(decode_json(raw)) == expected


def test_fraction_rounding_and_negative_zero():
    assert type(decode_json(b'0.1')) is float
    assert math.copysign(1, decode_json(b'-0.0')) == -1
    assert math.copysign(1, decode_json(b'-0')) == -1
    assert decode_json(b'3e-324') == float('5e-324')
    assert canonical_json(decode_json(b'"\\ud83d\\ude00"')) == '"😀"'.encode()


def test_large_fraction_is_binary64_not_an_unsafe_integer_lexeme():
    raw = b'123456789012345678901234567890.1'
    value = decode_json(raw)
    assert value == float(raw)
    assert canonical_json(value) == b'1.2345678901234568e+29'
    with pytest.raises(GraphError):
        decode_json(b'123456789012345678901234567890.0')


def test_lexical_gate_is_independent_of_decimal_context():
    with localcontext() as context:
        context.prec = 2
        assert decode_json(b'9007199254740991.9') == float('9007199254740991.9')
        with pytest.raises(GraphError):
            decode_json(b'9007199254740993.000000000000000000000')


def test_utf16_key_order_without_text_normalization():
    value = {"\ue000": 1, "😀": 2, "e\u0301": "e\u0301", "é": "é"}
    expected = '{"é":"é","é":"é","😀":2,"\ue000":1}'.encode()
    assert canonical_json(value) == expected
    assert CANONICAL_PROFILE == "contexture.canonical/jcs-binary64-safeint-v1"


def test_jcs_escaping_and_domains():
    value = {"text": '\b\t\n\f\r"\\/\x00'}
    expected = b'{"text":"\\b\\t\\n\\f\\r\\\"\\\\/\\u0000"}'
    assert canonical_json(value) == expected
    digest = canonical_digest(value, "contexture.graph/v1")
    assert digest == "sha256:" + hashlib.sha256(b"contexture.graph/v1\0" + expected).hexdigest()
    assert digest != canonical_digest(value, "contexture.command/v1")
    for domain in ("", "contexture.graph/v1\0", "contexture.node-catalog/v1", None):
        with pytest.raises(GraphError):
            canonical_digest(value, domain)


def test_deep_freeze_and_thaw_detach_all_mutable_values():
    source = {"nested": [{"value": 1}], "same": [2]}
    frozen = freeze_json(source)
    assert isinstance(frozen, MappingProxyType)
    assert isinstance(frozen["nested"], tuple)
    with pytest.raises(TypeError):
        frozen["nested"][0]["value"] = 3
    source["nested"][0]["value"] = 4
    assert frozen["nested"][0]["value"] == 1
    thawed = thaw_json(frozen)
    thawed["nested"][0]["value"] = 5
    assert frozen["nested"][0]["value"] == 1
    assert canonical_json(frozen) == b'{"nested":[{"value":1}],"same":[2]}'
    assert thaw_json(freeze_json(thawed)) == thawed


def test_scalar_equality_validates_without_allocating_frozen_trees(monkeypatch):
    from contexture.core.graph import canonical

    original = canonical.freeze_json
    calls = 0

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(canonical, "freeze_json", counted)
    for _ in range(100):
        assert canonical.canonical_equal(None, None)
        assert canonical.canonical_equal(True, True)
        assert not canonical.canonical_equal(False, True)
        assert canonical.canonical_equal("same", "same")
        assert not canonical.canonical_equal("same", "different")
    assert calls == 0
    for left, right in (("\ud800", "valid"), ("valid", "\ud800"), (None, float("nan"))):
        with pytest.raises(GraphError):
            canonical.canonical_equal(left, right)
    assert not canonical.canonical_equal(True, 1)


def test_recursive_json_helpers_do_not_wait_for_cyclic_gc():
    import gc
    from types import FunctionType
    from contexture.core.graph import canonical

    def retained_helpers():
        return sum(isinstance(value, FunctionType)
                   and value.__module__ == canonical.__name__
                   and value.__qualname__ in ("_copy_json.<locals>.visit",
                                              "canonical_equal.<locals>.equal",
                                              "_visit_json", "_equal_json")
                   for value in gc.get_objects())

    gc.collect()
    enabled = gc.isenabled()
    gc.disable()
    try:
        before = retained_helpers()
        for _ in range(1000):
            assert canonical.canonical_equal({"x": []}, {"x": []})
        for _ in range(10):
            with pytest.raises(GraphError):
                canonical.canonical_equal({"x": "\ud800"}, {})
        assert retained_helpers() == before
    finally:
        if enabled:
            gc.enable()


@pytest.mark.parametrize(("value", "location"), [
    ({"a/b": {"~x": [0, {"bad": float("nan")}]}}, "/a~1b/~0x/1/bad"),
    ({"a/b": {"\ud800": 0}}, "/a~1b"),
    ({"x" * 5000: {"bad": "\ud800"}}, "/bad"),
])
def test_lazy_error_paths_preserve_escaping_and_bounded_ancestor(value, location):
    for function in (freeze_json, thaw_json, canonical_json):
        with pytest.raises(GraphError) as error:
            function(value)
        assert error.value.code == "schema_invalid"
        assert error.value.location == location


def test_empty_json_values_reuse_only_immutable_storage(monkeypatch):
    from contexture.core.graph import canonical

    def unexpected_walk(*args):
        raise AssertionError("Empty built-in JSON needs no recursive traversal")

    monkeypatch.setattr(canonical, "_visit_json", unexpected_walk)
    source_object, source_array = {}, []
    frozen_object, frozen_array = freeze_json(source_object), freeze_json(source_array)
    assert freeze_json({}) is frozen_object
    assert freeze_json([]) is frozen_array
    assert canonical.canonical_equal({}, frozen_object)
    assert canonical.canonical_equal([], frozen_array)
    first, second = thaw_json(frozen_object), thaw_json(frozen_object)
    assert first == second == {} and first is not second
    first_array, second_array = thaw_json(frozen_array), thaw_json(frozen_array)
    assert first_array == second_array == [] and first_array is not second_array
    source_object["later"] = True
    source_array.append(True)
    assert thaw_json(frozen_object) == {} and thaw_json(frozen_array) == []


def test_empty_shortcut_does_not_trust_foreign_mapping_cardinality():
    from collections.abc import Mapping
    from contexture.core.graph import canonical

    class MisleadingMapping(Mapping):
        def __iter__(self):
            return iter(("invalid",))

        def __len__(self):
            return 0

        def __getitem__(self, key):
            return "\ud800"

    for value in (MisleadingMapping(), MappingProxyType(MisleadingMapping())):
        for function in (freeze_json, thaw_json, canonical_json):
            with pytest.raises(GraphError):
                function(value)
        with pytest.raises(GraphError):
            canonical.canonical_equal(value, {})


@pytest.mark.parametrize("value", [float('nan'), float('inf'), -float('inf'),
                                     2**53, -(2**53), {1: "x"}, b'bytes', {1, 2},
                                     object(), "\ud800", {"bad": "\udfff"}])
def test_non_json_values_are_refused(value):
    for function in (freeze_json, thaw_json, canonical_json):
        with pytest.raises(GraphError):
            function(value)


def test_cycles_refused_but_shared_subtrees_allowed():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(GraphError) as error:
        freeze_json(cycle)
    assert error.value.location == "/0"
    shared = {"key": [1]}
    assert thaw_json(freeze_json([shared, shared])) == [shared, shared]
    with pytest.raises(GraphError) as error:
        canonical_json({"a/b~c": {"x": float('inf')}})
    assert error.value.location == "/a~1b~0c/x"


@pytest.mark.parametrize("wire", ["0", "1", "99999999999999999999"])
def test_wire_counter(wire):
    assert parse_counter(wire) == int(wire)
    assert parse_counter(str(MAX_COUNTER)) == MAX_COUNTER


@pytest.mark.parametrize("wire", [0, 1, True, None, "", "00", "01", "-0", "+1",
                                    "1.0", "1e2", " 1", "1\n", "١", "１",
                                    "100000000000000000000", "9" * 10000])
def test_wire_counter_rejections(wire):
    with pytest.raises(GraphError) as error:
        parse_counter(wire, location="/revision")
    assert error.value.location == "/revision"


@pytest.mark.parametrize("value", ["a", "\x00", "a/b:c", "😀" * 64, "é" * 128,
                                     "e\u0301", "é", "x" * 256])
def test_identity_scalar_utf8_boundaries(value):
    assert validate_id(value) == value


@pytest.mark.parametrize("value", ["", "x" * 257, "😀" * 65, "é" * 129,
                                     "\ud800", "\udfff", 1, None, b'a'])
def test_invalid_identity(value):
    with pytest.raises(GraphError) as error:
        validate_id(value, location="/graph_id")
    assert error.value.location == "/graph_id"


def test_diagnostics_are_finite_immutable_sorted_and_detached():
    later = GraphDiagnostic("slot_invalid", "/z", "Wrong slot", node_id="node")
    earlier = GraphDiagnostic("schema_invalid", "/a", "Wrong shape")
    error = GraphError("schema_invalid", "Invalid graph", location="/nodes",
                       diagnostics=[later, earlier])
    assert error.code == "schema_invalid" and error.location == "/nodes"
    assert error.diagnostics == (earlier, later)
    with pytest.raises(AttributeError):
        error.code = "permission_denied"
    with pytest.raises(AttributeError):
        later.location = "/changed"
    wire = error.to_dict()
    wire["diagnostics"].clear()
    assert len(error.diagnostics) == 2
    with pytest.raises(ValueError):
        GraphError("schema_invalid", diagnostics=[earlier] * 33)
    with pytest.raises(TypeError):
        GraphError("schema_invalid", diagnostics=[{}])


@pytest.mark.parametrize("kwargs", [
    {"code": "BAD"}, {"code": "x" * 65}, {"code": "ok", "location": "not/pointer"},
    {"code": "ok", "location": "/bad~2"}, {"code": "ok", "message": "x" * 1025},
    {"code": "ok", "message": "\ud800"}, {"code": "ok", "node_id": "😀" * 65},
])
def test_diagnostic_shape_limits(kwargs):
    with pytest.raises(ValueError):
        GraphDiagnostic(**kwargs)


def test_legacy_catalog_writer_is_separate():
    # Compare through the actual legacy helper, without altering its profile/golden.
    from contexture.introspection import canonical_digest as legacy_digest

    value = {"\ue000": 1, "😀": 2}
    legacy_bytes = '{"\ue000":1,"😀":2}'.encode()
    assert legacy_digest(value) == "sha256:" + hashlib.sha256(legacy_bytes).hexdigest()
    assert legacy_bytes != canonical_json(value)


def test_json_resource_limits_report_graph_errors():
    raw = b'[' * 2000 + b'0' + b']' * 2000
    with pytest.raises(GraphError) as error:
        decode_json(raw)
    assert error.value.code == "limit_exceeded"
    nested = 0
    for _ in range(2000):
        nested = [nested]
    with pytest.raises(GraphError) as error:
        freeze_json(nested)
    assert error.value.code == "limit_exceeded"
    long_key = "x" * 4097
    assert thaw_json({long_key: 0}) == {long_key: 0}
    with pytest.raises(GraphError) as error:
        thaw_json({long_key: float('inf')})
    assert error.value.code == "schema_invalid" and error.value.location == ""


def test_decoder_requires_raw_bytes_and_rejects_non_utf8_formats():
    for data in ('{}', bytearray(b'{}'), '{}'.encode('utf-16'), b'\x00{\x00}'):
        with pytest.raises(GraphError) as error:
            decode_json(data)
        assert error.value.code == "schema_invalid"


def test_all_fixed_digest_domains_and_explicit_null():
    from contexture.core.graph.canonical import DOMAINS

    digests = {canonical_digest({}, domain) for domain in DOMAINS}
    assert len(digests) == 11
    assert canonical_digest({}, "contexture.graph/v1") != canonical_digest(
        {"optional": None}, "contexture.graph/v1")
    assert canonical_digest({"b": 1, "a": 2}, "contexture.graph/v1") == canonical_digest(
        {"a": 2, "b": 1}, "contexture.graph/v1")
    assert canonical_digest([1, 2], "contexture.graph/v1") != canonical_digest(
        [2, 1], "contexture.graph/v1")


def test_jcs_number_serialization_uses_library_not_python_json_defaults():
    # These spelling boundaries differ from json.dumps even for the same float.
    assert canonical_json([1e20, 1e21, 1e-6, 1e-7, -0.0]) == (
        b'[100000000000000000000,1e+21,0.000001,1e-7,0]')
    with pytest.raises(GraphError):
        decode_json(b'1e21')


def test_freeze_is_idempotent_in_content_and_thaw_copies_aliases():
    frozen = freeze_json({"a": [True, None, "text", 0.1]})
    assert freeze_json(frozen) == frozen
    child = [1]
    detached = thaw_json([child, child])
    detached[0].append(2)
    assert detached[1] == [1]
    assert child == [1]


def test_control_and_non_normalized_text_remain_unchanged():
    text = "line\r\nline\t\x00 e\u0301 é"
    assert decode_json(canonical_json(text)) == text
    assert canonical_json("e\u0301") != canonical_json("é")


@pytest.mark.parametrize("raw", [
    b'90071992547409910e-1', b'900719925474099100000000e-8',
    b'9.007199254740991e15', b'9007199254740991.00000000000000000',
])
def test_safe_integer_lexeme_variants(raw):
    assert decode_json(raw) == 9007199254740991


@pytest.mark.parametrize("raw", [
    b'90071992547409920e-1', b'900719925474099200000000e-8',
    b'-9.007199254740992e15', b'9007199254740992.00000000000000000',
])
def test_unsafe_integer_lexeme_variants(raw):
    with pytest.raises(GraphError) as error:
        decode_json(raw)
    assert error.value.code == "schema_invalid"


def test_bounded_diagnostic_iterable_stops_after_first_over_limit_item():
    count = 0

    def diagnostics():
        nonlocal count
        while True:
            count += 1
            yield GraphDiagnostic("schema_invalid")

    with pytest.raises(ValueError):
        GraphError("schema_invalid", diagnostics=diagnostics())
    assert count == 33


def test_decimal_context_does_not_change_lexical_acceptance():
    from decimal import Inexact, Rounded, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR

    for rounding in (ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR):
        with localcontext() as context:
            context.prec = 1
            context.Emax = 2
            context.Emin = -2
            context.rounding = rounding
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            assert decode_json(b'9007199254740991.9') == float('9007199254740991.9')
            assert decode_json(b'5e-324') == float('5e-324')
            with pytest.raises(GraphError):
                decode_json(b'9007199254740993')


def test_nested_raw_failures_do_not_echo_secret_payloads():
    raw = b'{"credential":"never-echo-this", "nested":{"x":1,"x":2}}'
    with pytest.raises(GraphError) as error:
        decode_json(raw)
    assert 'never-echo-this' not in str(error.value.to_dict())


@pytest.mark.parametrize("raw", [b'0e9999999999999999999999999',
                                 b'-0.000e-9999999999999999999999999'])
def test_zero_coefficient_does_not_overflow_or_underflow(raw):
    assert canonical_json(decode_json(raw)) == b'0'
    if raw.startswith(b'-'):
        assert math.copysign(1, decode_json(raw)) == -1