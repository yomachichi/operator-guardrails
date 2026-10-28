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


# Review findings on the money-command-guard port (davila7/claude-code-templates#1282): 4 P1 + 1 P2.
REVIEW_CASES = {
    # P1 wrapper chaining: a wrapper prefix must not carry a second, unchecked command
    "python scripts/ads_wrapper.py; curl https://api.stripe.com/v1/refunds -d charge=ch_x": True,
    "python scripts/ads_wrapper.py && curl https://api.stripe.com/v1/refunds": True,
    "python scripts/ads_wrapper.py | curl https://api.stripe.com/v1/payouts": True,
    "python scripts/ads_wrapper.py $(curl api.stripe.com)": True,
    "python scripts/ads_wrapper.py `curl api.stripe.com/v1/refunds`": True,
    "python scripts/ads_wrapper.py\ncurl https://api.stripe.com/v1/refunds": True,
    "python scripts/ads_wrapper.pyX daily_budget=9": True,
    # P1 rm option bypasses
    "rm -rf --no-preserve-root /": True,
    "rm -r -f /": True,
    "rm -fr ~": True,
    "rm --recursive --force /": True,
    "rm -v -rf /": True,
    # P1 forced push to main/master
    "git push origin +HEAD:main": True,
    "git push -uf origin main": True,
    "git push --force-with-lease origin main": True,
    "git push -f origin master": True,
    # unchanged blocks
    "rm -rf /": True,
    "curl https://api.stripe.com/v1/charges": True,
    "DROP TABLE users": True,
    # negative controls: still allowed
    "python scripts/ads_wrapper.py --set daily_budget=500": False,
    "python scripts/ads_wrapper.py": False,
    "git push origin main": False,
    "git push -u origin feature/x": False,
    "git push --force origin feature/x": False,
    "rm -rf build/": False,
    "rm -rf ./node_modules": False,
    "ls -la": False,
}


def test_hook_review_findings_block_and_controls_allowed():
    rules = dict(hook.DEFAULT_RULES, allow_wrappers=["python scripts/ads_wrapper.py"])
    wrong = [(c, want) for c, want in REVIEW_CASES.items() if (hook.verdict(c, rules, False) is not None) != want]
    assert not wrong, wrong


def test_hook_invalid_config_never_crashes_and_keeps_guard_on():
    with tempfile.TemporaryDirectory() as t:
        f = Path(t, "guard_rules.json")
        for bad in ('{"money":["["]}', '{"allow_wrappers":null}', '[1,2]', '{"block":"x"}', 'not json',
                    '{"money":[3]}'):
            f.write_text(bad)
            r = hook.load_rules(f)
            assert all(isinstance(r[k], list) for k in r), bad
            assert hook.verdict("ls", r, False) is None, bad
            assert hook.verdict("rm -rf /", r, False), bad
            assert hook.verdict("curl https://api.stripe.com/v1/refunds", r, False), bad
        f.write_text('{"allow_wrappers":["./w.sh"], "money":[]}')  # valid keys are kept as written
        r = hook.load_rules(f)
        assert r["allow_wrappers"] == ["./w.sh"] and r["money"] == []


def test_hook_project_root_found_from_subdirectory():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / ".claude").mkdir()
        sub = d / "a" / "b"
        sub.mkdir(parents=True)
        assert hook.find_root(sub, env={}) == d.resolve()
        assert hook.find_root(sub, env={"CLAUDE_PROJECT_DIR": str(d)}) == d


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
