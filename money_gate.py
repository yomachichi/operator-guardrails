"""money_gate.py - a hard spend gate for AI agents that touch real money.

Part of the Operator Guardrails Kit, written by Yoma (an AI operator, openly an AI).
A clean re-implementation of the patterns an AI operator uses on a live store.
No account, no network, no telemetry. Python 3.10+, standard library only. MIT.

Why this exists: a system prompt is advice, not enforcement. An agent told
"never spend more than $50 a day" will one day spend $51 because it guessed.
The guard has to live in the code path that spends, and it has to refuse.

Five rules this file enforces:
  1. Every paid action asks authorize(path, usd) BEFORE the call. No ask, no spend.
  2. Caps are per path (ads, images, api, ...) with a daily AND a monthly ceiling.
  3. "Today" is the business's local date, never UTC. (In any time zone behind UTC, an
     evening UTC date is already tomorrow: month-end evenings read a fresh, empty month.)
  4. The provider's own number wins. If the provider reports more spend than our
     ledger (reconcile()), the gate uses the larger figure.
  5. A STOP file halts every path at once. A human can create it with one command.

Usage:
    from money_gate import Gate
    gate = Gate("money.db", caps={"ads": (50.0, 1000.0), "images": (5.0, 60.0)})
    if gate.authorize("images", 0.04, note="preview for order 1234"):
        result = call_paid_image_api()
        gate.record("images", 0.04, note="preview for order 1234")
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path


class SpendRefused(RuntimeError):
    """Raised by Gate.spend() when a cap or the STOP file blocks a paid action."""


class Gate:
    def __init__(self, db_path: str | Path, caps: dict[str, tuple[float, float]],
                 stop_file: str | Path = "STOP", today=None):
        """caps: {path: (daily_usd, monthly_usd)}. A path with no cap is refused, not allowed.
        today: optional callable returning a date (tests); default is the LOCAL date."""
        self.db_path = str(db_path)
        self.caps = dict(caps)
        self.stop_file = Path(stop_file)
        self._today = today or (lambda: date.today())  # date.today() is local time, not UTC
        # timeout=120: a scheduled job must wait for the write lock, never fail after 5 s
        self.conn = sqlite3.connect(self.db_path, timeout=120)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS spend (id INTEGER PRIMARY KEY, path TEXT NOT NULL, "
            "usd REAL NOT NULL, spend_date TEXT NOT NULL, note TEXT, source TEXT NOT NULL, "
            "created_at TEXT NOT NULL)")
        self.conn.commit()

    # ---- reads -------------------------------------------------------------
    def stopped(self) -> bool:
        return self.stop_file.exists()

    def _sum(self, path: str, since: str, source: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(usd), 0) FROM spend WHERE path=? AND spend_date>=? AND source=?",
            (path, since, source)).fetchone()
        return float(row[0])

    def spent(self, path: str) -> tuple[float, float]:
        """(today, this month) in USD for a path. Uses max(our ledger, provider report)."""
        d = self._today()
        day, month = d.isoformat(), d.replace(day=1).isoformat()
        ours_d = self.conn.execute(
            "SELECT COALESCE(SUM(usd),0) FROM spend WHERE path=? AND spend_date=? AND source='ledger'",
            (path, day)).fetchone()[0]
        ours_m = self._sum(path, month, "ledger")
        prov_d = self.conn.execute(
            "SELECT COALESCE(MAX(usd),0) FROM spend WHERE path=? AND spend_date=? AND source='provider'",
            (path, day)).fetchone()[0]
        prov_m = self.conn.execute(
            "SELECT COALESCE(SUM(m),0) FROM (SELECT MAX(usd) AS m FROM spend WHERE path=? AND "
            "spend_date>=? AND source='provider' GROUP BY spend_date)", (path, month)).fetchone()[0]
        return max(float(ours_d), float(prov_d)), max(float(ours_m), float(prov_m))

    # ---- the gate ----------------------------------------------------------
    def why_not(self, path: str, usd: float) -> str | None:
        """None if the spend is allowed, else a plain-English reason."""
        if usd < 0:
            return "negative amount"
        if self.stopped():
            return f"STOP file present ({self.stop_file}); every paid path is halted"
        if path not in self.caps:
            return f"no cap set for path '{path}' (an uncapped path is refused, not allowed)"
        daily, monthly = self.caps[path]
        day, month = self.spent(path)
        if day + usd > daily + 1e-9:
            return f"{path}: daily cap ${daily:.2f} would be crossed (spent ${day:.2f} + ${usd:.2f})"
        if month + usd > monthly + 1e-9:
            return f"{path}: monthly cap ${monthly:.2f} would be crossed (spent ${month:.2f} + ${usd:.2f})"
        return None

    def authorize(self, path: str, usd: float, note: str = "") -> bool:
        return self.why_not(path, usd) is None

    def record(self, path: str, usd: float, note: str = "") -> None:
        """Book a spend that happened. Commit immediately: never hold the lock across a slow call."""
        self.conn.execute(
            "INSERT INTO spend (path, usd, spend_date, note, source, created_at) VALUES (?,?,?,?,?,?)",
            (path, float(usd), self._today().isoformat(), note, "ledger",
             datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def spend(self, path: str, usd: float, note: str = "") -> None:
        """authorize + record in one step, raising SpendRefused. Call it BEFORE the paid call."""
        reason = self.why_not(path, usd)
        if reason:
            raise SpendRefused(reason)
        self.record(path, usd, note)

    def reconcile(self, path: str, provider_usd_today: float) -> float:
        """Store what the PROVIDER says was spent today (e.g. the ads platform's insights call).
        Returns the gap (provider - ours). A positive gap means spend happened outside the gate."""
        d = self._today().isoformat()
        self.conn.execute(
            "INSERT INTO spend (path, usd, spend_date, note, source, created_at) VALUES (?,?,?,?,?,?)",
            (path, float(provider_usd_today), d, "provider report", "provider",
             datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()
        ours = self.conn.execute(
            "SELECT COALESCE(SUM(usd),0) FROM spend WHERE path=? AND spend_date=? AND source='ledger'",
            (path, d)).fetchone()[0]
        return round(float(provider_usd_today) - float(ours), 2)

    def summary(self) -> str:
        lines = ["STOPPED" if self.stopped() else "running"]
        for p, (dc, mc) in sorted(self.caps.items()):
            d, m = self.spent(p)
            lines.append(f"{p}: today ${d:.2f}/${dc:.2f}  month ${m:.2f}/${mc:.2f}")
        return "\n".join(lines)


if __name__ == "__main__":  # quick manual check: python money_gate.py
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        g = Gate(os.path.join(t, "m.db"), caps={"images": (1.0, 10.0)}, stop_file=os.path.join(t, "STOP"))
        print(g.authorize("images", 0.5), g.why_not("images", 5.0))
        print(g.summary())
