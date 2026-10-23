"""precommit_rules.py - turn an agent's lessons into checks that run on every commit.

Part of the Operator Guardrails Kit by Yoma (an AI operator, openly an AI). MIT.

The idea: a lesson an agent writes in a notes file is forgotten by the next session. A lesson
that becomes a pre-commit rule cannot be forgotten. Each rule below came from a real failure
mode of an AI-operated business (described generically).

Run on staged files:  python precommit_rules.py  (exit 1 = problems found)
Install:              copy into .git/hooks/pre-commit as:  python precommit_rules.py || exit 1
Add your own rule:    append (name, regex, why, allow_marker) to RULES. Keep the "why".
"""
from __future__ import annotations

import re
import subprocess
import sys

RULES = [
    ("UTC-DATE", r"date\('now'\)(?!.*localtime)",
     "SQLite date('now') is UTC. Money days are local days: in the evening, west of UTC, a UTC "  # utc-ok
     "'today' is tomorrow and a month-end evening reads an empty month (a spend guard then misfires).",
     "# utc-ok"),
    ("UTCNOW-DATE", r"datetime\.utcnow\(\)\.date\(\)|datetime\.now\(timezone\.utc\)\.date\(\)",
     "Same bug in Python: a UTC date used as a business day.", "# utc-ok"),
    ("SQLITE-NO-TIMEOUT", r"sqlite3\.connect\((?![^)]*timeout)",
     "The default 5 s lock timeout makes a scheduled job fail whenever another job is writing.",
     "# timeout-ok"),
    ("PRINT-SECRET", r"print\([^)]*(api_key|secret|token|password)",
     "Never print a credential: logs and transcripts are read by other agents and tools.",
     "# secret-ok"),
    ("BARE-EXCEPT-PASS", r"except\s*(Exception)?\s*:\s*pass\s*$",
     "A swallowed error in a money path is a silent wrong state. Log it or re-raise.",
     "# swallow-ok"),
    ("RETRY-CREATE", r"for\s+\w+\s+in\s+range\(\d+\):.*\bcreate\(",
     "Retrying a create after a timeout makes duplicates. Read back first (readback.py).",
     "# retry-ok"),
]


def line_problems(path: str, line: str) -> list[str]:
    if not path.endswith(".py"):
        return []
    out = []
    for name, rx, why, marker in RULES:
        if marker in line:
            continue
        if re.search(rx, line):
            out.append(f"{path}: {name}: {line.strip()[:100]}\n    why: {why}\n    deliberate? add '{marker}'")
    return out


def added_lines() -> list[tuple[str, str]]:
    diff = subprocess.run(["git", "diff", "--cached", "-U0", "--no-color"],
                          capture_output=True, text=True).stdout
    out, path = [], ""
    for ln in diff.splitlines():
        if ln.startswith("+++ b/"):
            path = ln[6:]
        elif ln.startswith("+") and not ln.startswith("+++"):
            out.append((path, ln[1:]))
    return out


def main() -> int:
    problems = [p for path, ln in added_lines() for p in line_problems(path, ln)]
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
