#!/usr/bin/env python3
"""pretool_money_guard.py - Claude Code PreToolUse hook that blocks money-moving and destructive
shell commands unless they go through an approved wrapper.

Part of the Operator Guardrails Kit by Yoma (an AI operator, openly an AI). MIT.

How it works: Claude Code sends the pending tool call as JSON on stdin. Exit code 0 lets it run.
Exit code 2 blocks it, and the text on stderr is shown to the agent as the reason.

Plugin build: rules come from <project>/.claude/money-guard.json when that file exists, otherwise
the built-in defaults below. Keys (same as the repo's hooks/guard_rules.example.json):
  "block":  regexes that are always refused (e.g. dropping a production database)
  "money":  regexes for calls that spend money (ads budget edits, paid API scripts, refunds)
  "allow_wrappers": command prefixes that are allowed to do money things because they call
                    money_gate.Gate.spend() themselves (e.g. "python scripts/ads_wrapper.py").
                    A wrapper only approves a single plain command: if anything is chained after it
                    (; && || |, backticks, $( ), redirects, newlines) it is not treated as a wrapper.
Any key left out of the file, or holding an invalid value (not a list of strings, or a regex that
does not compile), keeps its default.
A STOP file in the project root halts every money command at once. The project root is
$CLAUDE_PROJECT_DIR when Claude Code sets it, else the nearest parent of the working directory
holding .claude/ or .git, so a `cd` into a subdirectory never loses the rules or the STOP file.

Installed by the plugin's hooks/hooks.json (PreToolUse, matcher Bash). Nothing is sent anywhere.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_RULES = {
    "block": [
        r"\bDROP\s+(TABLE|DATABASE)\b",
        r"--no-preserve-root\b",
        # rm with a recursive flag anywhere among its options (-rf, -r -f, -fr, --recursive, extra
        # options in between) aimed at / ~ $HOME or .
        r"\brm\b(?=[^;&|\n]*\s(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b)(?=[^;&|\n]*\s(/|~/?|\$HOME/?|\.)(\s|$))",
        # forced push to main/master: --force, --force-with-lease, any short-flag group containing f
        # (-f, -uf), or a +refspec (+HEAD:main)
        r"\bgit\s+push\b(?=[^;&|\n]*(--force|\s-[a-zA-Z]*f[a-zA-Z]*\b|\s\+\S*))(?=[^;&|\n]*\b(main|master)\b)",
        r"\b(volume|database|db)\s+(delete|destroy)\b",
    ],
    "money": [
        r"\bdaily_budget\b", r"\blifetime_budget\b", r"/refunds?\b", r"\bpayouts?\b",
        r"api\.stripe\.com", r"graph\.facebook\.com/.*/(adsets|campaigns|ads)\b",
        r"\bdiscount_?codes?\b", r"\bprice_rules?\b",
    ],
    "allow_wrappers": [],
}


CHAIN = re.compile(r"[;&|`<>\n]|\$\(")


def _valid_list(value, compile_rx: bool) -> bool:
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        return False
    if compile_rx:
        try:
            for x in value:
                re.compile(x)
        except re.error:
            return False
    return True


def load_rules(path: Path | None = None) -> dict:
    """Never raises. Missing, unreadable or non-object file -> defaults; an invalid key (wrong type,
    bad regex) -> that key's default, so a typo can never switch the guard off or crash every call."""
    p = path or HERE / "guard_rules.json"
    try:
        if not p.exists():
            return DEFAULT_RULES
        rules = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_RULES  # a broken rules file falls back to the defaults, never to "allow all"
    if not isinstance(rules, dict):
        return DEFAULT_RULES
    out = {}
    for key, default in DEFAULT_RULES.items():
        value = rules.get(key, default)
        ok = value == [] or _valid_list(value, compile_rx=(key != "allow_wrappers"))
        out[key] = value if ok else default
    return out


def find_root(cwd, env=None) -> Path:
    """$CLAUDE_PROJECT_DIR if set, else the nearest parent of cwd holding .claude/ or .git, else cwd."""
    env = os.environ if env is None else env
    if env.get("CLAUDE_PROJECT_DIR"):
        return Path(env["CLAUDE_PROJECT_DIR"])
    here = Path(cwd or ".").resolve()
    for d in (here, *here.parents):
        if (d / ".claude").is_dir() or (d / ".git").exists():
            return d
    return here


def verdict(command: str, rules: dict, stop_exists: bool) -> str | None:
    """None = allow. Otherwise the reason to block."""
    for rx in rules["block"]:
        if re.search(rx, command, re.IGNORECASE):
            return f"BLOCKED (destructive pattern /{rx}/). Ask the human; do not look for a workaround."
    stripped = command.strip()
    # a wrapper approves ONE plain command; anything chained after it would otherwise ride along unchecked
    is_wrapper = not CHAIN.search(stripped) and any(
        stripped == w or stripped.startswith(w + " ") for w in rules["allow_wrappers"])
    if is_wrapper and stop_exists:  # a wrapper is a money command by definition
        return "BLOCKED: STOP file present; every money action is halted until a human removes it."
    for rx in rules["money"]:
        if re.search(rx, command, re.IGNORECASE):
            if stop_exists:
                return "BLOCKED: STOP file present; every money action is halted until a human removes it."
            if not is_wrapper:
                return (f"BLOCKED (money pattern /{rx}/). Money moves only through an approved wrapper "
                        f"that calls money_gate.Gate.spend(): {rules['allow_wrappers'] or 'none configured'}. "
                        "Do not retry with a different spelling.")
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0  # not our business; never break the session on bad input
    if event.get("tool_name") != "Bash":
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    root = find_root(event.get("cwd"))
    reason = verdict(command, load_rules(root / ".claude" / "money-guard.json"), (root / "STOP").exists())
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
