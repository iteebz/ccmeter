# ccmeter

Reverse-engineer Anthropic's opaque Claude subscription limits into hard numbers. Anthropic shows utilization as a percentage but won't say a percentage of what. ccmeter cross-references utilization ticks against local token logs to derive tokens-per-percent and cost-per-percent — the actual dollar value of each plan.

## Architecture

```
ccmeter/
  auth.py    — reads OAuth creds from OS keychain (macOS Keychain, Linux libsecret)
  cli.py     — fncli entry point: poll, report, history, status, install, uninstall
  db.py      — sqlite connection + auto-migration (~/.ccmeter/meter.db)
  display.py — ANSI colors, progress bar, formatting primitives (shared by all commands)
  poll.py    — usage API poller with change detection and exponential backoff
  scan.py    — JSONL scanner: reads per-message token counts from ~/.claude/projects/
  report.py  — cross-references usage ticks against token windows to derive cost-per-percent
  history.py — display raw usage samples
  status.py  — collection health and per-bucket current state
  daemon.py  — launchd (macOS) / systemd (Linux) install/uninstall
  update.py  — version checking and self-update from PyPI
  migrations/ — sqlite schema migrations (001_initial, 002_scan_cache)
docs/
  evidence.md — sourced incidents of opaque limit changes
```

Zero external deps beyond `fncli`. stdlib `urllib` for HTTP.

## Key facts

- Usage API: `api.anthropic.com/api/oauth/usage` with header `anthropic-beta: oauth-2025-04-20`
- macOS keychain: `security find-generic-password -a $USER -s "Claude Code-credentials" -w`
- Credential blob: `{claudeAiOauth: {accessToken, refreshToken, expiresAt, subscriptionType, rateLimitTier}}`
- Known buckets: `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `extra_usage`
- `extra_usage` uses `used_credits` instead of `utilization`
- Null buckets (e.g. `iguana_necktie`) are feature flags — we capture everything the API returns
- JSONL location: `~/.claude/projects/<project>/<session_id>.jsonl`
- JSONL assistant messages contain `.message.usage` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`
- Session metadata: `~/.claude/usage-data/session-meta/<session_id>.json` (token counts in thousands)

## Token weighting

Raw token totals are misleading. A session that's 99% cache reads looks like millions of tokens, but cache reads are 10x cheaper than input tokens. The report leads with **cost-per-percent** (API-equivalent USD) as the headline metric — this is what Anthropic likely weights by internally. Pricing used for weighting:

- Opus: $5 input, $25 output, $0.50 cache_read, $6.25 cache_create (per MTok)
- Sonnet: $1.50 input, $7.50 output, $0.15 cache_read, $1.875 cache_create
- Haiku: $0.40 input, $2 output, $0.04 cache_read, $0.50 cache_create

## Conventions

- fncli for CLI (not click/argparse)
- pyright strict for type checking
- stdlib over deps
- sqlite for local storage
- print() for output
- Deferred imports in CLI handlers (fast startup)
- All ANSI/display through `display.py` — never inline escape codes

## Development

```bash
uv sync                    # install deps
uv run ccmeter --help      # run dev version
uv run ccmeter poll --once # single poll to verify auth works
uv run ccmeter report      # test report generation
just format                # ruff format + fix
just lint                  # ruff check
just typecheck             # pyright strict
just test                  # pytest
just ci                    # lint + typecheck + test
```

`uv run ccmeter` runs the local dev version from source. `ccmeter` in PATH should point to the latest PyPI release (via `pip install ccmeter` or `uv tool install ccmeter`). Never symlink dev into PATH.

Test against real data — the tool reads from your local `~/.claude/` and OS keychain. No mocks needed for integration testing. Unit tests should mock the keychain and API calls.
