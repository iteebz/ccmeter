# ccmeter

Measure what Anthropic won't tell you: what Claude subscription limits actually mean in tokens.

## Architecture

```
ccmeter/
  auth.py    — reads OAuth creds from OS keychain (macOS Keychain, Linux libsecret)
  cli.py     — fncli entry point: poll, report, history, status, install, uninstall
  db.py      — sqlite schema (~/.ccmeter/meter.db)
  poll.py    — usage API poller with change detection and exponential backoff
  scan.py    — JSONL scanner: reads per-message token counts from ~/.claude/projects/
  report.py  — cross-references usage ticks against token windows to derive tokens-per-percent
  history.py — display raw usage samples
  status.py  — collection health
  daemon.py  — launchd (macOS) / systemd (Linux) install/uninstall
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

## Conventions

- fncli for CLI (not click/argparse)
- stdlib over deps
- sqlite for local storage
- print() for output
- Deferred imports in CLI handlers
- `just format` / `just lint`

## Development

```bash
uv sync                    # install deps
uv run ccmeter --help      # run locally
uv run ccmeter poll --once # single poll to verify auth works
uv run ccmeter report      # test report generation
just format                # ruff format + fix
just lint                  # ruff check
just test                  # pytest
```

Test against real data — the tool reads from your local `~/.claude/` and OS keychain. No mocks needed for integration testing. Unit tests should mock the keychain and API calls.

## Roadmap

- [ ] Confidence intervals on calibration (need more data)
- [ ] Anonymous contribution: `ccmeter export` dumps standardized JSON for community sharing
- [ ] Community dataset repo for aggregated calibration data
- [ ] Windows support (Windows Credential Manager)
- [ ] PyPI publish
