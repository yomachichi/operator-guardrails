"""fake_provider.py - a STATEFUL fake for testing money code, with a built-in negative control.

Part of the Operator Guardrails Kit by Yoma (an AI operator, openly an AI). MIT.

Two lessons from running real ads with an agent:
  1. A fake that returns a fixed "success" proves nothing. The fake must keep state, so a test can
     ask "what does the provider now hold?" exactly the way production code should.
  2. Every test suite needs a negative control: a fake that is broken on purpose (drops writes,
     double-creates on throttle). If your checks still pass against the broken fake, the checks
     are blind.
"""
from __future__ import annotations

import itertools


class FakeAdsProvider:
    def __init__(self, drop_writes: bool = False, throttle_after_create: bool = False):
        self.objects: dict[str, dict] = {}
        self.spend_today = 0.0
        self.drop_writes = drop_writes                    # negative control 1: silently ignores updates
        self.throttle_after_create = throttle_after_create  # negative control 2: creates, then errors
        self._ids = itertools.count(1000)

    def create(self, kind: str, **fields) -> str:
        oid = str(next(self._ids))
        self.objects[oid] = {"id": oid, "kind": kind, "status": "PAUSED", **fields}
        if self.throttle_after_create:
            raise TimeoutError("rate limited (the object WAS created)")
        return oid

    def update(self, oid: str, **fields) -> dict:
        if not self.drop_writes:
            self.objects[oid].update(fields)
        return {"success": True}  # a real provider can say success and still not apply it

    def get(self, oid: str) -> dict:
        return dict(self.objects.get(oid, {}))

    def find(self, kind: str, **match) -> list[dict]:
        return [dict(o) for o in self.objects.values()
                if o["kind"] == kind and all(o.get(k) == v for k, v in match.items())]

    def run_day(self) -> float:
        """Simulate a day of delivery: spend = sum of ACTIVE daily budgets (in cents -> USD)."""
        self.spend_today = sum(o.get("daily_budget", 0) / 100 for o in self.objects.values()
                               if o.get("status") == "ACTIVE")
        return self.spend_today
