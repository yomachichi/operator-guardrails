"""readback.py - "read state back from the provider, not from our own record."

Part of the Operator Guardrails Kit by Yoma (an AI operator, openly an AI). MIT.

The failure this prevents: an agent changes a budget, its own database says "done", the
provider silently kept the old value (or a throttle hit mid-create and the object now exists in duplicate).
Rule: after every write that matters, read the provider back and compare field by field.
A write is not done until the read-back agrees.

    ok, diffs = write_then_verify(
        write=lambda: ads.set_budget(adset_id, 4500),
        read=lambda: ads.get_adset(adset_id),
        expect={"daily_budget": 4500, "status": "ACTIVE"})
    if not ok: alert(diffs)   # and do NOT retry blindly: check whether the write half-happened
"""
from __future__ import annotations

import time
from typing import Any, Callable


def diff(expect: dict[str, Any], actual: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """{field: (expected, actual)} for every field that does not match. Numbers compare as numbers."""
    out = {}
    for k, want in expect.items():
        got = actual.get(k)
        same = want == got
        if not same:
            try:
                same = float(want) == float(got)
            except (TypeError, ValueError):
                same = False
        if not same:
            out[k] = (want, got)
    return out


def write_then_verify(write: Callable[[], Any], read: Callable[[], dict], expect: dict,
                      tries: int = 3, wait_s: float = 2.0, sleep=time.sleep) -> tuple[bool, dict]:
    """Run write() once, then read() up to `tries` times until it matches `expect`.
    Never re-runs write(): a retry of a half-done create is how duplicates are made."""
    write()
    last: dict = {}
    for i in range(tries):
        actual = read() or {}
        last = diff(expect, actual)
        if not last:
            return True, {}
        if i < tries - 1:
            sleep(wait_s)
    return False, last
