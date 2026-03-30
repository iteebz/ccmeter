# ccmeter

Reverse-engineer Anthropic's opaque Claude subscription limits into hard numbers.

## The problem

Anthropic doesn't publish what their usage percentages actually mean in tokens. Subscribers across Free, Pro ($20), Max 5x ($100), and Max 20x ($200) tiers have no way to know:

- What "1%" of a weekly or session limit costs in tokens
- Whether caps have changed between billing cycles
- How their usage compares to what their plan actually provides

The only official communication on limit changes has been a single engineer's tweet thread, days after users started hitting walls they'd never hit before at identical usage levels.

The community is operating on vibes. This tool gives them data.

## The pattern

Three times now, same structure:

1. **Announce generous temporary promotion** (Christmas 2x, March off-peak 2x)
2. **Silently tighten baseline limits** during or immediately after the promotion window
3. **Attribute complaints to "contrast effect"** when users notice

### Christmas 2x (Dec 25, 2025 – Jan 5, 2026)

Anthropic ran a 2x usage promotion for the holidays. When it expired, users reported limits felt tighter than pre-promotion baseline. A Claude Code user provided The Register with screenshots showing roughly 60% reduction in token limits. Anthropic dismissed it as users "reacting to the withdrawal of bonus usage."

A Max plan subscriber who'd rarely hit limits filed a GitHub bug report on January 4 — hitting rate limits within an hour of normal usage since January 1st.

### Third-party tool crackdown (January 9, 2026)

Anthropic deployed safeguards blocking third-party tools using subscription OAuth tokens. No warning. No migration path. Some users reported being banned within 20 minutes of starting a task on the $200/month Max plan. DHH called the move "very customer hostile."

### March 2x off-peak (Mar 13–28, 2026)

Five-hour usage doubled during off-peak hours. But during the same period, Anthropic silently tightened peak-hour session limits. A $200 Max subscriber posted screenshots showing session usage climbing from 52% to 68% to 91% within minutes.

The explanation: ~7% of users would now hit limits they wouldn't have before. Pro subscribers most affected.

## How it works

Anthropic's OAuth usage API returns utilization percentages at 1% granularity:

```json
{
  "five_hour":        {"utilization": 16.0, "resets_at": "..."},
  "seven_day":        {"utilization": 19.0, "resets_at": "..."},
  "seven_day_sonnet": {"utilization": 100.0, "resets_at": "..."},
  "seven_day_opus":   null,
  "seven_day_cowork": null,
  "iguana_necktie":   null,
  "extra_usage":      {"is_enabled": true, "used_credits": 0.0}
}
```

Claude Code stores per-message token counts locally in JSONL conversation files under `~/.claude/projects/`.

**ccmeter** polls the usage API on a heartbeat and correlates percentage ticks against token counts from sessions that ran between samples. When utilization jumps from 15% to 16%, and you consumed N tokens in that window, then 1% ≈ N tokens for that bucket and tier.

Track that number over time. If it shrinks, Anthropic tightened the cap.

### Known confounds

- **Multi-surface usage**: claude.ai, Claude Code, and Cowork share the same limits but only Claude Code has local JSONL token logs. Users on multiple surfaces simultaneously will pollute calibration data.
- **1% granularity**: The API reports whole percentages only. Calibration is approximate and improves with more samples over longer periods.
- **Bucket interactions**: Some buckets may share underlying quotas in ways not reflected by the API structure.

## The community layer

Individual calibration is useful. Aggregated calibration across hundreds of users is power.

ccmeter optionally submits anonymized data points to a public dataset:

- **Included**: tier, bucket, tokens-per-percent-tick, timestamp
- **Not included**: no accounts, no PII, no conversation content, no OAuth tokens

With enough contributors, the community gets a continuously updated reference showing real token budgets per tier per bucket — and can detect cap changes within days rather than weeks of confused Reddit threads.

## Install

```bash
pip install ccmeter
# or
uv pip install ccmeter
```

## Usage

```bash
# Start collecting — polls usage API every 2 min, writes to local sqlite
ccmeter poll

# Show what you've collected
ccmeter history

# Calculate what 1% actually means in tokens
ccmeter calibrate

# Check collection status
ccmeter status
```

## What it is

A CLI instrument. Polls locally, stores locally, optionally contributes to a public anonymous dataset.

## What it is not

A platform. No accounts. No frontend. No social features. The community builds dashboards and analysis on top of the public data. The tool stays a thermometer.

## Why it matters

Anthropic's stated values include transparency. Their current subscription model is the opposite — opaque limits, silent changes, promotional shell games where off-peak "bonuses" mask baseline nerfs.

ccmeter doesn't attack Anthropic. It holds them to their own standard. If the caps are fair, the data will show it. If they're not, the data will show that too.

## License

MIT
