import math

import pytest

from canopyproof.canonical import CanonicalError, canonical_bytes, dec, document_hash, fixed_int


def test_sorted_compact_ascii():
    assert canonical_bytes({"b": 1, "a": {"d": "x", "c": [True, None]}}) == b'{"a":{"c":[true,null],"d":"x"},"b":1}'


@pytest.mark.parametrize("bad", [{"x": 0.5}, {"x": "é"}, {"x": 2**60}, {1: "a"}, {"x": object()}])
def test_rejects_values_that_are_not_portable(bad):
    with pytest.raises(CanonicalError):
        canonical_bytes(bad)


def test_document_hash_is_order_independent():
    assert document_hash({"a": "1", "b": "2"}) == document_hash({"b": "2", "a": "1"})
    assert document_hash({"a": "1"}) != document_hash({"a": "1.0"})


def test_dec_rounding_and_missing():
    assert dec(0.12345, 4) == "0.1234"  # banker's rounding on the decimal repr
    assert dec(0.12355, 4) == "0.1236"
    assert dec(-0.00001) == "0.0000"
    assert dec(None) is None and dec(math.nan) is None and dec(math.inf) is None
    assert dec(12, 1) == "12.0"


def test_fixed_int():
    assert fixed_int("0.6123") == 6123
    assert fixed_int("-39.9", 100) == -3990
    assert fixed_int("-0.0001") == -1
    assert fixed_int(None) is None
