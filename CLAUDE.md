# ccmeter

Reverse-engineer Anthropic's opaque Claude subscription limits into hard numbers. Anthropic shows utilization as a percentage but won't say a percentage of what. ccmeter cross-references utilization ticks against local token logs to derive tokens-per-percent and cost-per-percent — the actual dollar value of each plan.

## Architecture

```
ccmeter/
  poll.py    — daemon core: fetches usage API, status-aware retry, pidfile lock, health.json
  scan.py    — JSONL scanner: parses token counts from ~/.claude/projects/, zlib-compressed cache
  report.py  — cross-references usage ticks against token windows to derive cost-per-percent
  auth.py    — reads OAuth creds from OS keychain (macOS Keychain, Linux libsecret)
  cli.py     — fncli entry point: poll, report, account, history, status, trend, install, uninstall
  config.py  — local config: ~/.ccmeter/config.json (account pinning)
  db.py      — sqlite connection + auto-migration (~/.ccmeter/meter.db)
  display.py — ANSI colors, progress bar, formatting primitives (shared by all commands)
  activity.py — tool call and LOC extraction from JSONL during scan
  status.py  — daemon health, collection freshness, per-bucket burn rate
  history.py — raw usage sample display
  trend.py   — braille sparkline chart of budget over time
  daemon.py  — launchd (macOS) / systemd (Linux) install/uninstall
  update.py  — version checking and self-update from PyPI
  migrations/ — sqlite schema migrations (001-004)
```

Zero external deps beyond `fncli`. stdlib `urllib` for HTTP.

## Daemon

The daemon (`poll.py`) is the most important component. Everything else can be derived from its data. Design decisions:

- **Pidfile lock**: `fcntl.LOCK_EX` on `~/.ccmeter/poll.pid` prevents duplicate pollers
- **Status-aware retry**: 429 → respects full Retry-After header (no cap), 401/403 → immediate credential refresh + 30s retry, network/5xx → exponential backoff capped at 5m
- **Health file**: `~/.ccmeter/health.json` — atomically written every poll cycle. Single snapshot with ok/fail state and last 5 errors. Read by `ccmeter status`. No growth.
- **Log rotation**: truncates `poll.log`/`poll.err` to last 64KB when they exceed 512KB, runs once at startup
- **Credential refresh**: 401/403 triggers immediate keychain re-read. Other failures trigger refresh after 3 consecutive failures as fallback
- **Account pinning**: if `~/.ccmeter/config.json` has `account_id`, poller skips recording when active credential doesn't match. Prevents data pollution on shared machines.

## Local state

```
~/.ccmeter/
  meter.db      — sqlite: usage_samples, scan_cache, budget_history
  config.json   — account pin, user preferences
  health.json   — daemon health snapshot (written atomically each cycle)
  poll.pid      — pidfile lock
  poll.log      — daemon stdout (launchd/systemd)
  poll.err      — daemon stderr
```

## Usage API

- Endpoint: `api.anthropic.com/api/oauth/usage` with header `anthropic-beta: oauth-2025-04-20`
- macOS keychain: `security find-generic-password -a $USER -s "Claude Code-credentials" -w`
- Credential blob: `{claudeAiOauth: {accessToken, refreshToken, expiresAt, subscriptionType, rateLimitTier}}`
- Known buckets: `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus`, `seven_day_cowork`, `seven_day_oauth_apps`, `extra_usage`
- `extra_usage` uses `used_credits` instead of `utilization`
- Null buckets (e.g. `iguana_necktie`) are feature flags — we capture everything the API returns
- Profile endpoint: `api.anthropic.com/api/oauth/profile` returns `account.uuid` for multi-account filtering

## Rate limit architecture (from Claude Code source)

All five buckets run simultaneously. Any one can reject a request independently. See `docs/source.md` for full details.

### Early warning thresholds (used in burn rate prediction)

- 5h window: warn at 90% utilization when ≤72% of window elapsed
- 7d window: graduated — 25%/15%, 50%/35%, 75%/60%

### Known confounders

- **Effort level**: Opus 4.6 defaults to medium effort for Max subscribers. Medium = fewer output tokens = less budget. Not observable from JSONL.
- **Fast mode**: higher throughput Opus, uses overage budget. Not distinguishable from standard sessions in JSONL.
- **Multi-surface usage**: claude.ai and cowork share limits but only Claude Code has local token logs.

## JSONL data

- Location: `~/.claude/projects/<project>/<session_id>.jsonl`
- Assistant messages contain `.message.usage` with `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`
- Session metadata: `~/.claude/usage-data/session-meta/<session_id>.json` (token counts in thousands)
- Scan cache uses zlib compression (92% reduction). Cache version bump invalidates all rows and vacuums

## Token weighting

Raw token totals are misleading. A session that's 99% cache reads looks like millions of tokens, but cache reads are 10x cheaper than input tokens. The report leads with **cost-per-percent** (API-equivalent USD) as the headline metric — confirmed by Claude Code source to be how Anthropic weights budget internally. Pricing from platform.claude.com/docs/en/about-claude/pricing:

- Opus 4.6: $5 input, $25 output, $0.50 cache_read, $6.25/$10.00 cache_create (5m/1h TTL)
- Sonnet 4.6: $3 input, $15 output, $0.30 cache_read, $3.75/$6.00 cache_create (5m/1h TTL)
- Haiku 4.5: $1 input, $5 output, $0.10 cache_read, $1.25/$2.00 cache_create (5m/1h TTL)

ccmeter uses the 5m cache write price. JSONL doesn't distinguish TTL. ~8% underestimate if all writes use 1h caching.

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
uv run ccmeter account     # show account info
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

Good scopes: `poll`, `scan`, `report`, `status`, `auth`, `daemon`, `display`, `activity`, `trend`, `history`, `update`, `db`, `config`. Omit scope for cross-cutting changes.

```
fix(poll): status-aware retry instead of blind exponential backoff
feat(status): add collection health
fix(scan): compress cache with zlib
refactor: tighten types and fix code quality nits
```
