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
    # fix 1: rm -rf blocks only root / home / current-dir targets, not every absolute path
    check("rm -rf / blocked", run("rm -rf /", d), 2)
    check("rm -rf ~ blocked", run("rm -rf ~", d), 2)
    check("rm -rf ~/ blocked", run("rm -rf ~/", d), 2)
    check("rm -rf $HOME blocked", run("rm -rf $HOME", d), 2)
    check("rm -rf . blocked", run("rm -rf .", d), 2)
    check("rm -fr / blocked", run("sudo rm -fr / --no-preserve-root", d), 2)
    check("rm -rf /tmp/build allowed", run("rm -rf /tmp/build", d), 0)
    check("rm -rf ./dist allowed", run("rm -rf ./dist", d), 0)
    check("rm -rf ~/project/node_modules allowed", run("rm -rf ~/project/node_modules", d), 0)
    # fix 2: force-push to main/master caught with the flag before OR after the branch
    check("git push --force origin main blocked", run("git push --force origin main", d), 2)
    check("git push origin main --force blocked", run("git push origin main --force", d), 2)
    check("git push -f origin master blocked", run("git push -f origin master", d), 2)
    check("git push origin master -f blocked", run("git push origin master -f", d), 2)
    check("git push origin feature allowed", run("git push origin feature", d), 0)
    check("git push origin main (no force) allowed", run("git push origin main", d), 0)
    check("git push --force origin feature allowed", run("git push --force origin feature", d), 0)
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
