"""Canonical JSON and job keys (module protocol: `job_key = H(module_id, compat, key_inputs)`).

The canonical form is **RFC 8785 (JSON Canonicalization Scheme)**, so any language computes the same bytes, plus three
refusals: NaN and infinities, integers beyond ±2^53 (send those as decimal strings), and non-string object keys. The
shared vectors in spec/vectors/canonical-json.json and job-key.json pin it down for every implementation.

A module that enqueues jobs through the `jobs.enqueue` effect computes their keys with `job_key`, so the host's result
cache and replica checks treat equal inputs as the same job, and a `compat` bump makes every key new. Stage jobs of a
chain append `:<stage>`.
"""
import hashlib
import math

MAX_SAFE_INT = 2 ** 53


class CanonicalError(ValueError):
    pass


def _number(x) -> str:
    if isinstance(x, bool):
        raise CanonicalError("bool is not a number")
    if isinstance(x, int):
        if abs(x) > MAX_SAFE_INT:
            raise CanonicalError(f"integer {x} is beyond ±2^53: send it as a decimal string")
        return str(x)
    if not math.isfinite(x):
        raise CanonicalError("NaN and infinities have no canonical form")
    if x == 0:
        return "0"
    # ECMAScript Number.prototype.toString over the shortest round-trip digits (RFC 8785 §3.2.2.3)
    r = repr(abs(x))
    if "e" in r:
        mant, exp = r.split("e")
        exp = int(exp)
    else:
        mant, exp = r, 0
    if "." in mant:
        ip, fp = mant.split(".")
    else:
        ip, fp = mant, ""
    digits = (ip + fp).lstrip("0")
    lead_zeros = len(ip + fp) - len((ip + fp).lstrip("0"))
    n = len(ip) + exp - lead_zeros                  # position of the decimal point relative to `digits`
    digits = digits.rstrip("0") or "0"
    k = len(digits)
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        s = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return ("-" if x < 0 else "") + s


_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _utf16_key(s: str):
    return s.encode("utf-16-be")


def canonical_json(obj) -> str:
    """RFC 8785 canonical JSON (see the module docstring for the three refusals)."""
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, (int, float)):
        return _number(obj)
    if isinstance(obj, str):
        return _string(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(canonical_json(v) for v in obj) + "]"
    if isinstance(obj, dict):
        for k in obj:
            if not isinstance(k, str):
                raise CanonicalError(f"object key {k!r} is not a string")
        items = sorted(obj.items(), key=lambda kv: _utf16_key(kv[0]))
        return "{" + ",".join(_string(k) + ":" + canonical_json(v) for k, v in items) + "}"
    raise CanonicalError(f"{type(obj).__name__} has no JSON form")


def canonical_bytes(obj) -> bytes:
    return canonical_json(obj).encode("utf-8")


def job_key(module_id: str, compat: str, key_inputs: dict, stage: str | None = None) -> str:
    h = hashlib.sha256(canonical_bytes({"module": module_id, "compat": compat, "inputs": key_inputs})).hexdigest()
    return f"{h}:{stage}" if stage else h
