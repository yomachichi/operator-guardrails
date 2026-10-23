#!/usr/bin/env python3
"""pretool_money_guard.py - Claude Code PreToolUse hook that blocks money-moving and destructive
shell commands unless they go through an approved wrapper.

Part of the Operator Guardrails Kit by Yoma (an AI operator, openly an AI). MIT.

How it works: Claude Code sends the pending tool call as JSON on stdin. Exit code 0 lets it run.
Exit code 2 blocks it, and the text on stderr is shown to the agent as the reason.

Configure in guard_rules.json next to this file (see guard_rules.example.json):
  "block":  regexes that are always refused (e.g. dropping a production database)
  "money":  regexes for calls that spend money (ads budget edits, paid API scripts, refunds)
  "allow_wrappers": command prefixes that are allowed to do money things because they call
                    money_gate.Gate.spend() themselves (e.g. "python scripts/ads_wrapper.py")
A STOP file in the project root halts every money command at once.

Install (project .claude/settings.json):
  {"hooks": {"PreToolUse": [{"matcher": "Bash",
     "hooks": [{"type": "command", "command": "python hooks/pretool_money_guard.py"}]}]}}
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_RULES = {
    "block": [
        r"\bDROP\s+(TABLE|DATABASE)\b",
        r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(/|~|\$HOME|\.\s*$)",
        r"\bgit\s+push\b.*--force\b.*\b(main|master)\b",
        r"\b(volume|database|db)\s+(delete|destroy)\b",
    ],
    "money": [
        r"\bdaily_budget\b", r"\blifetime_budget\b", r"/refunds?\b", r"\bpayouts?\b",
        r"api\.stripe\.com", r"graph\.facebook\.com/.*/(adsets|campaigns|ads)\b",
        r"\bdiscount_?codes?\b", r"\bprice_rules?\b",
    ],
    "allow_wrappers": [],
}


def load_rules(path: Path | None = None) -> dict:
    p = path or HERE / "guard_rules.json"
    if p.exists():
        rules = json.loads(p.read_text(encoding="utf-8"))
        return {k: rules.get(k, DEFAULT_RULES[k]) for k in DEFAULT_RULES}
    return DEFAULT_RULES


def verdict(command: str, rules: dict, stop_exists: bool) -> str | None:
    """None = allow. Otherwise the reason to block."""
    for rx in rules["block"]:
        if re.search(rx, command, re.IGNORECASE):
            return f"BLOCKED (destructive pattern /{rx}/). Ask the human; do not look for a workaround."
    stripped = command.strip()
    is_wrapper = any(stripped.startswith(w) for w in rules["allow_wrappers"])
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
    root = Path(event.get("cwd") or ".")
    reason = verdict(command, load_rules(), (root / "STOP").exists())
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
