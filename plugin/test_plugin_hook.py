"""Smoke tests for the plugin build of pretool_money_guard.py (positive + negative controls)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = sys.argv[1]


def run(cmd, cwd, tool="Bash", project_dir=None):
    ev = {"tool_name": tool, "tool_input": {"command": cmd}, "cwd": str(cwd)}
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project_dir:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    p = subprocess.run([sys.executable, HOOK], input=json.dumps(ev), capture_output=True, text=True, env=env)
    return p.returncode


fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("PASS" if ok else "FAIL"), name, "exit", got, "want", want)


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d / ".git").mkdir()  # mark d as the project root so the root lookup never walks above it
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

# Review findings on the money-command-guard port (davila7/claude-code-templates#1282): 4 P1 + 1 P2.
W = "python3 scripts/ads_wrapper.py"
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    (d / ".claude").mkdir()
    cfg = d / ".claude" / "money-guard.json"
    cfg.write_text(json.dumps({"allow_wrappers": [W]}))
    # P1 wrapper chaining
    check("wrapper ; refund blocked", run(W + "; curl https://api.stripe.com/v1/refunds -d charge=ch_x", d), 2)
    check("wrapper && refund blocked", run(W + " && curl https://api.stripe.com/v1/refunds", d), 2)
    check("wrapper | payout blocked", run(W + " | curl https://api.stripe.com/v1/payouts", d), 2)
    check("wrapper $( ) blocked", run(W + " $(curl api.stripe.com)", d), 2)
    check("wrapper newline refund blocked", run(W + "\ncurl https://api.stripe.com/v1/refunds", d), 2)
    check("wrapper prefix without space blocked", run(W + "X daily_budget=9", d), 2)
    check("plain wrapper with money args allowed", run(W + " --set daily_budget=500", d), 0)
    check("bare wrapper allowed", run(W, d), 0)
    # P1 rm option bypasses
    check("rm -rf --no-preserve-root / blocked", run("rm -rf --no-preserve-root /", d), 2)
    check("rm -r -f / blocked", run("rm -r -f /", d), 2)
    check("rm --recursive --force / blocked", run("rm --recursive --force /", d), 2)
    check("rm -v -rf / blocked", run("rm -v -rf /", d), 2)
    check("rm -rf build/ allowed", run("rm -rf build/", d), 0)
    check("rm -rf ./node_modules allowed", run("rm -rf ./node_modules", d), 0)
    # P1 forced push variants
    check("git push origin +HEAD:main blocked", run("git push origin +HEAD:main", d), 2)
    check("git push -uf origin main blocked", run("git push -uf origin main", d), 2)
    check("git push --force-with-lease origin main blocked", run("git push --force-with-lease origin main", d), 2)
    check("git push -u origin feature/x allowed", run("git push -u origin feature/x", d), 0)
    # P1 project root: a subdirectory still finds the project's rules and STOP file
    sub = d / "a" / "b"
    sub.mkdir(parents=True)
    check("subdir: project wrapper rule still applies", run(W + " --set daily_budget=5", sub), 0)
    (d / "STOP").write_text("")
    check("subdir: STOP in project root halts the wrapper", run(W + " --set daily_budget=5", sub), 2)
    with tempfile.TemporaryDirectory() as other:
        check("CLAUDE_PROJECT_DIR: STOP found from any cwd", run(W, other, project_dir=d), 2)
    (d / "STOP").unlink()
    # P2 invalid config values: never crash, keep the guard on (per-key fallback to defaults)
    for bad in ('{"money":["["]}', '{"allow_wrappers":null}', '[1,2]', '{"block":"x"}', '{"money":[3]}'):
        cfg.write_text(bad)
        check(f"bad config {bad}: refund blocked", run("curl https://api.stripe.com/v1/refunds", d), 2)
        check(f"bad config {bad}: rm -rf / blocked", run("rm -rf /", d), 2)
        check(f"bad config {bad}: ls allowed", run("ls -la", d), 0)

print("ALL PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
