"""Canonical JSON for hashed records.

Floats are rejected on purpose: Python and JavaScript print some floats differently, which would
make the same record hash differently in the browser verifier. Measured values are therefore stored
as fixed-precision decimal strings (see `dec`) or integers. Strings must be ASCII.
"""
import json
import math
from decimal import ROUND_HALF_EVEN, Decimal

from eth_hash.auto import keccak


class CanonicalError(ValueError):
    pass


def _check(value, path="$"):
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise CanonicalError(f"{path}: integer outside the JavaScript safe range")
        return
    if isinstance(value, float):
        raise CanonicalError(f"{path}: floats are not allowed; use dec() strings")
    if isinstance(value, str):
        if not value.isascii():
            raise CanonicalError(f"{path}: strings must be ASCII")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise CanonicalError(f"{path}: keys must be ASCII strings")
            _check(item, f"{path}.{key}")
        return
    raise CanonicalError(f"{path}: unsupported type {type(value).__name__}")


def canonical_bytes(value) -> bytes:
    """Sorted keys, no insignificant whitespace, ASCII only. Same bytes as the dashboard's JS."""
    _check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def keccak_hex(data: bytes) -> str:
    return "0x" + keccak(data).hex()


def document_hash(value) -> str:
    return keccak_hex(canonical_bytes(value))


def dec(value, places=4):
    """Fixed-precision decimal string (banker's rounding), or None for missing/non-finite values."""
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    quantum = Decimal(1).scaleb(-places)
    out = Decimal(repr(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)
    if out == 0:
        out = abs(out)  # never emit "-0.0000"
    return format(out, "f")


def fixed_int(value, scale=10_000):
    """Integer fixed-point used in on-chain leaves, derived from the decimal string."""
    if value is None:
        return None
    return int((Decimal(value) * scale).to_integral_value(rounding=ROUND_HALF_EVEN))
