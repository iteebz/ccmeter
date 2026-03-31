# ccmeter

Reverse-engineer Anthropic's opaque Claude subscription limits into hard numbers. Anthropic shows utilization as a percentage but won't say a percentage of what. ccmeter cross-references utilization ticks against local token logs to derive tokens-per-percent and cost-per-percent — the actual dollar value of each plan.

## Architecture

```
ccmeter/
  poll.py    — daemon core: fetches usage API, status-aware retry, pidfile lock
  scan.py    — JSONL scanner: parses token counts from ~/.claude/projects/, zlib-compressed cache
  report.py  — cross-references usage ticks against token windows to derive cost-per-percent
  auth.py    — reads OAuth creds from OS keychain (macOS Keychain, Linux libsecret)
  cli.py     — fncli entry point: poll, report, history, status, install, uninstall
  db.py      — sqlite connection + auto-migration (~/.ccmeter/meter.db)
  display.py — ANSI colors, progress bar, formatting primitives (shared by all commands)
  activity.py — tool call and LOC extraction from JSONL during scan
  status.py  — daemon health, collection freshness, per-bucket state
  history.py — raw usage sample display
  trend.py   — braille sparkline chart of budget over time
  daemon.py  — launchd (macOS) / systemd (Linux) install/uninstall
  update.py  — version checking and self-update from PyPI
  migrations/ — sqlite schema migrations (001_initial, 002_scan_cache, 003_budget_history)
```

Zero external deps beyond `fncli`. stdlib `urllib` for HTTP.

## Daemon

The daemon (`poll.py`) is the most important component. Everything else can be derived from its data. Design decisions:

- **Pidfile lock**: `fcntl.LOCK_EX` on `~/.ccmeter/poll.pid` prevents duplicate pollers (which would cause duplicate DB rows and doubled rate limit risk)
- **Status-aware retry**: 429 → short fixed delay (Retry-After or 60s), 401/403 → immediate credential refresh + 30s retry, network/5xx → exponential backoff capped at 5m. A single 429 should never create a multi-minute data gap
- **Log rotation**: truncates `poll.log`/`poll.err` to last 64KB when they exceed 512KB, runs once at startup
- **Credential refresh**: 401/403 triggers immediate keychain re-read. Other failures trigger refresh after 3 consecutive failures as fallback

## Usage API

- Endpoint: `api.anthropic.com/api/oauth/usage` with header `anthropic-beta: oauth-2025-04-20`
- macOS keychain: `security find-generic-password -a $USER -s "Claude Code-credentials" -w`
- Credential blob: `{claudeAiOauth: {accessToken, refreshToken, expiresAt, subscriptionType, rateLimitTier}}`
- Known buckets: `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `extra_usage`
- `extra_usage` uses `used_credits` instead of `utilization`
- Null buckets (e.g. `iguana_necktie`) are feature flags — we capture everything the API returns

## JSONL data

- Location: `~/.claude/projects/<project>/<session_id>.jsonl`
- Assistant messages contain `.message.usage` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`
- Session metadata: `~/.claude/usage-data/session-meta/<session_id>.json` (token counts in thousands)
- Scan cache uses zlib compression (92% reduction). Cache version bump invalidates all rows and vacuums

## Token weighting

Raw token totals are misleading. A session that's 99% cache reads looks like millions of tokens, but cache reads are 10x cheaper than input tokens. The report leads with **cost-per-percent** (API-equivalent USD) as the headline metric — this is what Anthropic likely weights by internally. Pricing used for weighting:

- Opus: $5 input, $25 output, $0.50 cache_read, $6.25 cache_create (per MTok)
- Sonnet: $3 input, $15 output, $0.30 cache_read, $3.75 cache_create
- Haiku: $1 input, $5 output, $0.10 cache_read, $1.25 cache_create

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

## Pre-push invariant

`just ci` must pass before every push. No exceptions. If lint, typecheck, or tests fail, fix them before pushing.

## Commit messages

Format: `tag(scope): verb object` — scope is the **module**, not the project. `ccmeter` is never a useful scope because everything is ccmeter.

Good scopes: `poll`, `scan`, `report`, `status`, `auth`, `daemon`, `display`, `activity`, `trend`, `history`, `update`, `db`. Omit scope for cross-cutting changes.

```
fix(poll): status-aware retry instead of blind exponential backoff
feat(status): add collection health
fix(scan): compress cache with zlib
refactor: tighten types and fix code quality nits
```
