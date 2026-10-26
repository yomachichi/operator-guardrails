"""Smoke tests for the plugin build of pretool_money_guard.py (positive + negative controls)."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1]


def run(cmd, cwd, tool="Bash"):
    ev = {"tool_name": tool, "tool_input": {"command": cmd}, "cwd": str(cwd)}
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(ev), capture_output=True, text=True)
    return p.returncode


fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("PASS" if ok else "FAIL"), name, "exit", got, "want", want)


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    check("refund curl blocked (defaults)", run("curl -X POST https://api.stripe.com/v1/refunds", d), 2)
    check("plain ls allowed", run("ls -la", d), 0)
    check("DROP TABLE blocked", run('psql -c "DROP TABLE users"', d), 2)
    check("non-Bash tool ignored", run("DROP TABLE x", d, tool="Read"), 0)
    (d / ".claude").mkdir()
    (d / ".claude" / "money-guard.json").write_text(json.dumps(
        {"allow_wrappers": ["python scripts/ads_wrapper.py"], "money": ["--budget"]}))
    check("project rules: wrapper allowed", run("python scripts/ads_wrapper.py --budget 5", d), 0)
    check("project rules: non-wrapper money blocked", run("node x.js --budget 5", d), 2)
    (d / "STOP").write_text("")
    check("STOP halts the wrapper", run("python scripts/ads_wrapper.py --budget 5", d), 2)
    (d / "STOP").unlink()
    (d / ".claude" / "money-guard.json").write_text("not json")
    check("broken rules file -> defaults still block", run("curl https://api.stripe.com/v1/charges", d), 2)
    p = subprocess.run([sys.executable, HOOK], input="garbage", capture_output=True, text=True)
    check("bad stdin never breaks the session", p.returncode, 0)

print("ALL PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
