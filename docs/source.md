# source findings: claude code rate limit internals

confirmed implementation details from claude code v2.1.88 source. not speculation — see [incidents.md](incidents.md) for the external record.

## the five buckets

all run simultaneously. any one can independently reject a request.

| bucket | window | internal name | header prefix |
|--------|--------|---------------|---------------|
| session | rolling 5h | `five_hour` | `anthropic-ratelimit-unified-5h-` |
| weekly aggregate | rolling 7d | `seven_day` | `anthropic-ratelimit-unified-7d-` |
| weekly opus | rolling 7d | `seven_day_opus` | via `representative-claim` |
| weekly sonnet | rolling 7d | `seven_day_sonnet` | via `representative-claim` |
| extra usage | monthly | `overage` | `anthropic-ratelimit-unified-overage-` |

source: `claudeAiLimits.ts:29-35`

## per-response utilization headers

every inference API response includes real-time utilization — higher resolution than the usage API.

```
anthropic-ratelimit-unified-5h-utilization         float 0-1
anthropic-ratelimit-unified-5h-reset               unix epoch seconds
anthropic-ratelimit-unified-5h-surpassed-threshold float (when warning fires)
anthropic-ratelimit-unified-7d-utilization         float 0-1
anthropic-ratelimit-unified-7d-reset               unix epoch seconds
anthropic-ratelimit-unified-7d-surpassed-threshold float
anthropic-ratelimit-unified-overage-utilization    float 0-1
anthropic-ratelimit-unified-overage-surpassed-threshold float
```

source: `claudeAiLimits.ts:164-179`

ccmeter cannot access these. claude code consumes them internally and does not write them to JSONL. available via interactive and headless sessions alike — but only the client sees them. a proxy or the 1-token haiku probe (see below) could capture them directly.

## early warning thresholds

the client computes warnings from time-relative utilization ratios. these are the exact values ccmeter uses in burn rate prediction (`report.py:EARLY_WARNINGS`).

**5h window:** warn at 90% utilization when ≤72% of window elapsed

**7d window (graduated):**
- 25% utilization when ≤15% elapsed
- 50% utilization when ≤35% elapsed
- 75% utilization when ≤60% elapsed

source: `claudeAiLimits.ts:53-70`

## budget is cost-weighted

rate limits are NOT raw token counts. the client tracks cost using API pricing. cache reads are 10x cheaper than inputs — a session that's 90% cache hits costs ~10% of an uncached session for the same token count.

source: `claude.ts:48-57`

pricing confirmed at [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing):

| model | input | output | cache read | cache write 5m | cache write 1h |
|-------|-------|--------|------------|----------------|----------------|
| opus 4.6 | $5 | $25 | $0.50 | $6.25 | $10 |
| sonnet 4.6 | $3 | $15 | $0.30 | $3.75 | $6 |
| haiku 4.5 | $1 | $5 | $0.10 | $1.25 | $2 |

note: JSONL reports `cache_creation_input_tokens` without distinguishing TTL. ccmeter uses the 5m price. if claude code uses 1h caching for system prompts (likely), cache write costs are underestimated by ~60% on those tokens — roughly ~8% of total cost per percent.

this validates ccmeter's cost-per-percent approach as the correct normalization.

## the status state machine

every inference response includes a status header:

```
anthropic-ratelimit-unified-status: allowed | allowed_warning | rejected
```

on rejection (429), additional headers identify the cause:
```
anthropic-ratelimit-unified-representative-claim: five_hour | seven_day | seven_day_opus | seven_day_sonnet
anthropic-ratelimit-unified-reset: <unix epoch>
anthropic-ratelimit-unified-fallback: available
retry-after: <seconds>
```

overage layer (when base budget exhausted):
```
anthropic-ratelimit-unified-overage-status: allowed | allowed_warning | rejected
anthropic-ratelimit-unified-overage-disabled-reason: <one of 12 enum values>
```

source: `claudeAiLimits.ts:376-436`

## retry hierarchy

1. 429 with retry-after < 20s → sleep and retry same model (preserves prompt cache)
2. 429 with retry-after > 20s → fast mode cooldown (10-30min), fall back to standard speed
3. 429 on opus, sonnet available → `FallbackTriggeredError`, switches to sonnet
4. 529 overloaded → 3 retries for foreground, 0 for background, then fallback
5. persistent mode (`CLAUDE_CODE_UNATTENDED_RETRY`) → retries indefinitely, 5min max backoff, capped at 6h

source: `withRetry.ts`

## x-should-retry: subscribers are on their own

for max/pro subscribers, the server sends `x-should-retry: true` but the client **ignores it** — the retry window would be hours, which is useless. only enterprise (PAYG) users respect it.

subscribers hitting a 429: the client shows the error and stops. no automatic retry. this means ccmeter data shows sharp utilization plateaus at the limit — utilization stops changing because no more calls are being made.

source: `withRetry.ts:737-742`

## effort level

opus 4.6 defaults to medium effort for pro, max, and team subscribers. medium = less thinking = fewer output tokens = less budget consumed. not observable from JSONL — a known confounder for cost-per-percent.

source: `effort.ts:307-319`

## quota pre-check

interactive sessions send a 1-token haiku call at startup to populate rate limit state:
```
model=haiku, max_tokens=1, messages=[{role:'user', content:'quota'}]
```

non-interactive sessions (`-p` flag) skip this entirely — they get utilization from their first real call's response headers.

source: `claudeAiLimits.ts:199-249`

## key constants

```
SHORT_RETRY_THRESHOLD_MS  = 20s      retry with same model below this
MIN_COOLDOWN_MS           = 10min    minimum fast mode cooldown
DEFAULT_FAST_MODE_HOLD_MS = 30min    default fast mode cooldown
PERSISTENT_MAX_BACKOFF_MS = 5min     max backoff for unattended retry
PERSISTENT_RESET_CAP_MS   = 6h      max total wait for persistent retry
MAX_529_RETRIES           = 3        before model fallback
DEFAULT_MAX_RETRIES       = 10       total retry attempts
BASE_DELAY_MS             = 500ms    exponential backoff base
HEARTBEAT_INTERVAL_MS     = 30s      keep-alive during long waits
```

## what this means for ccmeter

**integrated:**
- early warning thresholds → burn rate prediction (shipped)
- cost-weighting confirmation → validates our approach
- bucket architecture → all confirmed buckets tracked
- rolling window semantics → informs burn rate math
- pricing → confirmed against official docs

**not observable:**
- per-response utilization headers (not in JSONL)
- effort level per session
- fast mode vs standard mode
- model fallback events
- cache write TTL (5m vs 1h)

**future options:**
- 1-token haiku probe for sub-minute utilization (cheap, returns utilization headers directly)
- proxy to capture per-response headers (invasive, fragile)
