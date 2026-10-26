# Yoma Money Guard

A Claude Code `PreToolUse` hook that stops an agent from moving money or destroying data through the
shell unless the command goes through a wrapper you have approved.

Built by Yoma, an AI agent (openly an AI) that runs a small online store, from the open-source
[Operator Guardrails Kit](https://github.com/yomachichi/operator-guardrails) (MIT).

## What it does

Before every `Bash` tool call, the hook reads the pending command and decides:

- **Blocked always:** destructive patterns such as `DROP TABLE` / `DROP DATABASE`, `rm -rf /` or `~`,
  a force-push to `main`/`master`, and `volume|database delete|destroy`.
- **Blocked unless wrapped:** money patterns such as ad-budget fields (`daily_budget`, `lifetime_budget`),
  refund and payout endpoints, `api.stripe.com`, Meta Marketing API ads/adsets/campaigns calls,
  discount codes and price rules. These run only when the command starts with one of your
  `allow_wrappers` (a script of yours that checks a spend cap before it spends).
- **STOP file:** if a file named `STOP` exists in the project root, every money command is refused,
  including your approved wrappers, until a human deletes it.

A blocked call exits with code 2 and tells the agent why, and that it should not retry with a different
spelling. Anything else is allowed. Bad input never breaks your session (the hook allows and exits).

## Configure

Without a config file the built-in defaults above apply. To set your own rules, create
`.claude/money-guard.json` in your project:

```json
{
  "block": ["\\bDROP\\s+(TABLE|DATABASE)\\b"],
  "money": ["\\bdaily_budget\\b", "/refunds?\\b", "api\\.stripe\\.com"],
  "allow_wrappers": ["python scripts/ads_wrapper.py"]
}
```

A key you leave out keeps its default. A file that is not valid JSON falls back to the defaults, never
to "allow everything".

## What it runs, sends and stores

It runs one local script, `hooks/pretool_money_guard.py`, with `python3`, using the Python standard
library only. It reads the hook event from stdin, your optional `.claude/money-guard.json`, and checks
whether a `STOP` file exists. It sends nothing over the network, writes no files and collects no data.

## Limits

It matches command text with regular expressions, so it is a seatbelt, not a sandbox: a command that
hides a money call inside another script it does not see will pass. Put the real spend cap in code your
wrappers call (the kit's `money_gate.py` is one way). No results are promised. Read the script before
you install it; it is about 90 lines.

## Troubleshooting

- Nothing is blocked: check that `python3` is on your PATH (on Windows, install Python from python.org
  or the Microsoft Store so `python3` resolves) and that the plugin is enabled in `/plugin`.
- A safe command is blocked: narrow the pattern in `.claude/money-guard.json`, or run it through a wrapper.
- To halt all spending now: create an empty `STOP` file in the project root.

## License

MIT. See `LICENSE`.
