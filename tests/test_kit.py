"""Tests for the Operator Guardrails Kit. Run: python -m pytest tests -q  (or python tests/test_kit.py)"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

from money_gate import Gate, SpendRefused  # noqa: E402
from readback import write_then_verify  # noqa: E402
from fake_provider import FakeAdsProvider  # noqa: E402
from precommit_rules import line_problems  # noqa: E402
import pretool_money_guard as hook  # noqa: E402


def _gate(tmp, caps=None, today=None):
    return Gate(os.path.join(tmp, "m.db"), caps=caps or {"ads": (50.0, 250.0)},
                stop_file=os.path.join(tmp, "STOP"), today=today)


def test_daily_cap_refuses():
    with tempfile.TemporaryDirectory() as t:
        g = _gate(t)
        g.spend("ads", 40)
        assert g.authorize("ads", 10)
        assert not g.authorize("ads", 10.01)
        try:
            g.spend("ads", 11)
            assert False, "should refuse"
        except SpendRefused as e:
            assert "daily cap" in str(e)
        g.conn.close()


def test_uncapped_path_is_refused_not_allowed():
    with tempfile.TemporaryDirectory() as t:
        g = _gate(t)
        assert "no cap" in g.why_not("refunds", 1)
        g.conn.close()


def test_monthly_cap_across_days():
    with tempfile.TemporaryDirectory() as t:
        day = [date(2026, 10, 1)]
        g = _gate(t, today=lambda: day[0])
        for d in range(1, 6):
            day[0] = date(2026, 10, d)
            g.spend("ads", 50)
        day[0] = date(2026, 10, 6)
        assert "monthly cap" in g.why_not("ads", 1)
        day[0] = date(2026, 11, 1)  # new month resets
        assert g.authorize("ads", 50)
        g.conn.close()


def test_stop_file_halts_everything():
    with tempfile.TemporaryDirectory() as t:
        g = _gate(t)
        Path(t, "STOP").write_text("owner said stop")
        assert "STOP" in g.why_not("ads", 0.01)
        g.conn.close()


def test_provider_number_wins_when_higher():
    with tempfile.TemporaryDirectory() as t:
        g = _gate(t)
        g.record("ads", 10)
        gap = g.reconcile("ads", 45)  # the platform says 45 spent: 35 happened outside the gate
        assert gap == 35
        assert not g.authorize("ads", 6)  # 45 + 6 > 50, even though our ledger says 10
        g.conn.close()


def test_readback_catches_dropped_write_negative_control():
    good, bad = FakeAdsProvider(), FakeAdsProvider(drop_writes=True)
    for prov, should_pass in ((good, True), (bad, False)):
        oid = prov.create("adset", daily_budget=3000)
        ok, diffs = write_then_verify(lambda: prov.update(oid, daily_budget=4500, status="ACTIVE"),
                                      lambda: prov.get(oid), {"daily_budget": 4500, "status": "ACTIVE"},
                                      sleep=lambda s: None)
        assert ok is should_pass, (should_pass, diffs)


def test_throttle_mid_create_is_visible_before_retry():
    prov = FakeAdsProvider(throttle_after_create=True)
    try:
        prov.create("ad", name="spring-sale")
    except TimeoutError:
        pass
    # the right move: look before re-creating
    assert len(prov.find("ad", name="spring-sale")) == 1


def test_precommit_rules():
    assert line_problems("a.py", "x = conn.execute(\"SELECT date('now')\")")  # utc-ok (test fixture)
    assert not line_problems("a.py", "x = conn.execute(\"SELECT date('now','localtime')\")")
    assert line_problems("a.py", "c = sqlite3.connect('x.db')")  # timeout-ok (test fixture)
    assert not line_problems("a.py", "c = sqlite3.connect('x.db', timeout=120)")
    assert line_problems("a.py", "print('key', api_key)")
    assert not line_problems("notes.txt", "date('now')")  # utc-ok (test fixture)


def test_hook_blocks_money_and_destructive_allows_wrapper():
    rules = dict(hook.DEFAULT_RULES, allow_wrappers=["python scripts/ads_wrapper.py"])
    assert hook.verdict("psql -c 'DROP TABLE orders'", rules, False)
    assert hook.verdict("curl -X POST https://graph.facebook.com/v21.0/123/adsets -d daily_budget=9999", rules, False)
    assert hook.verdict("python scripts/ads_wrapper.py set-budget 123 45", rules, False) is None
    assert hook.verdict("python scripts/ads_wrapper.py set-budget 123 45", rules, True)  # STOP beats wrapper
    assert hook.verdict("ls -la && git status", rules, False) is None


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    sys.exit(1 if fails else 0)
