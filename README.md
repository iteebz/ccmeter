# ccmeter

measure your actual claude code usage limits instead of guessing.

## why

anthropic shows you a percentage bar. 21% used. but 21% of *what*? they've never said.

twice in four months, limits changed during or after promotions. both times the community noticed. both times the explanation was "contrast effect." without numbers, you can't tell the difference between "i'm using more" and "they gave me less."

ccmeter gives you the number. track it over time. if it drops, the cap shrank. see [docs/incidents.md](docs/incidents.md) for the receipts.

## what it measures

from a max 20x subscriber running opus:

```
5h window:  $363 budget  = 20x × $18 pro base
7d window:  $1,900 budget  = 20x × $95 pro base
```

the dollar amount isn't about sub vs api. it's the only unit that makes different token types comparable — cache reads are 10x cheaper than input tokens, so raw totals are meaningless. cost-weighting normalizes everything into one number you can track.

every report stores the budget. next run shows the delta. if your budget drops 5% overnight, you see it in red. across enough users, a simultaneous drop is undeniable.

## how it works

1. **poll** — records utilization from anthropic's usage API every 2 minutes
2. **scan** — reads per-message token counts from claude code's local JSONL logs
3. **calibrate** — when utilization ticks from 15% to 16%, it knows what tokens were used in that window. cost-weight them. that's your budget per percent.

## install

```bash
pip install ccmeter
```

or clone and run directly:

```bash
git clone https://github.com/iteebz/ccmeter && cd ccmeter && uv sync
```

requires python 3.12+, claude code installed and signed in. macos and linux. zero dependencies beyond [fncli](https://pypi.org/project/fncli/).

## usage

```bash
ccmeter install          # background daemon, survives restarts
ccmeter report           # budget, burn rate, binding constraint
ccmeter report --json    # structured output for sharing
ccmeter status           # live utilization, burn rate, daemon health
ccmeter trend            # budget over time as a sparkline chart
ccmeter account          # show account info, plan, tier
ccmeter account --pin    # lock to current account (filters shared machines)
ccmeter history          # raw usage tick history
ccmeter poll --fast      # 60s interval for higher resolution
ccmeter uninstall        # remove daemon
```

needs a few days of data collection before calibration kicks in. install it, let it run, check back.

## claude code only

ccmeter reads token data from local session logs that only claude code produces. if you use claude.ai or cowork at the same time, token counts get inflated because the API tracks combined usage but we only see claude code's logs. for cleanest data, use claude code as your primary surface.

## what it collects

**from anthropic's API** (polled every 2 min, recorded on change):
- utilization percentage per bucket (`five_hour`, `seven_day`, etc.)
- reset timestamps
- subscription tier (detected from credentials)

**from claude code's local JSONL files** (scanned on `report`):
- per-message token counts: input, output, cache_read, cache_create
- model, timestamps, session id
- tool calls, reads, edits, bash commands, lines changed

**everything stays local** in `~/.ccmeter/meter.db`. your oauth token only goes to anthropic's own API — the same call claude code already makes.

## known confounds

- **multi-surface usage** — claude.ai, cowork, and claude code share limits but only claude code has local token logs. simultaneous use inflates counts.
- **1% granularity** — the API reports whole percentages only. more samples over longer periods = better accuracy.
- **cache write TTL** — anthropic charges different rates for 5-minute vs 1-hour cache writes. JSONL doesn't distinguish which was used. ccmeter uses the 5m price, which may underestimate by ~8% if claude code uses 1h caching.
- **effort level** — opus defaults to medium effort for max subscribers. medium = fewer output tokens = less budget. not observable from JSONL. sessions at high effort cost more per percent.
- **pro base is derived** — the pro base number is your budget divided by your tier multiplier. it's a prediction, not a measurement. a pro user running ccmeter would confirm it.

see [docs/source.md](docs/source.md) for confirmed implementation details from the claude code source.

## help

the more people running this across tiers (pro, max 5x, max 20x, team) and models (sonnet, opus, haiku), the harder it gets to change limits without anyone noticing.

install it. let the daemon run. share your `ccmeter report` output.

if you want to contribute code: see [CONTRIBUTING.md](CONTRIBUTING.md).

## license

MIT
