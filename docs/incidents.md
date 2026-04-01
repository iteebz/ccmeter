# incident log: reported limit changes in claude subscriptions

reported incidents where claude subscription limits appear to have changed. sources and claims, not proof — see [source.md](source.md) for confirmed implementation details from the claude code source. ccmeter exists to close the measurement gap described at the bottom.

## verified facts

these are not in dispute:

- anthropic does not publish token budgets for any subscription tier
- the usage API reports utilization as a percentage with no disclosed denominator
- no changelog exists for limit adjustments
- "20x usage" has no public definition of the base unit

## incident 1: post-christmas limit reports (dec 25, 2025 - jan 5, 2026)

**what happened:**

- anthropic ran a 2x usage promotion dec 25-31 for pro and max subscribers across claude.ai, claude code, and claude in chrome
- after expiration, users reported limits feeling tighter than pre-promotion baseline

**sources:**

- a claude code user provided The Register with screenshots showing roughly 60% reduction in token usage limits based on token-level analysis of local logs
- a max subscriber who'd never hit limits in three months [filed a bug report](https://github.com/anthropics/claude-code/issues/16157) on jan 3 after hitting limits within two hours. issue remains open.
- widespread reports on reddit, claude developers discord, and forums of accounts reaching maximum within minutes on previously normal tasks

**anthropic's response:** users are "reacting to the withdrawal of bonus usage awarded over the holidays"

**what's unverified:** whether baseline limits actually changed, or whether users were perceiving the contrast correctly. no independent measurement existed at the time.

## incident 2: march peak-hour limit change (mar 13-28, 2026)

**what happened:**

- from march 13-28, five-hour usage was doubled during off-peak hours (outside 8am-2pm ET weekdays)
- during the same period, users reported significantly tighter peak-hour session limits
- reports started around march 23

**sources:**

- $200/month max subscriber [posted screenshots](https://x.com/BradGroux/status/2036107512487751858) showing usage climbing from 52% to 91% within minutes ([covered by Piunikaweb](https://piunikaweb.com/2026/03/24/claude-max-subscribers-left-frustrated-after-usage-limits-drained-rapidly-with-no-clear-explanation/))
- [github issue](https://github.com/anthropics/claude-code/issues/38335) filed the same day
- $100/month max 5x subscriber [debated cancelling](https://www.reddit.com/r/ClaudeCode/comments/1s3b96m/debating_getting_rid_of_my_cc_max_membership/) over "the lack of transparency on this issue specifically"

**anthropic's response:** came days later via [tweet thread from Thariq](https://x.com/trq212/status/2037254607001559305), one engineer. ~7% of users would now hit limits they wouldn't have before. pro subscribers most affected. "overall weekly limits stay the same, just how they're distributed across the week is changing." framed as managing growing demand during peak hours.

**what's verified here:** anthropic confirmed they adjusted 5-hour session limits during peak hours. the existence of the adjustment is not speculation. the net effect (redistribution vs reduction) remains unverified.

## the measurement gap

both incidents highlight the same structural problem: users cannot distinguish between perception shifts and actual limit changes because there is no independent measurement.

- no published token budgets per tier
- no versioned changelog for limit adjustments
- no public definition of what the multiplier (5x, 20x) applies to
- usage API reports percentages of an undisclosed number

without measurement, every limit complaint is dismissable as anecdotal. with measurement, a synchronized budget drop across independent machines is data.

## what ccmeter does about this

ccmeter converts usage percentages into a cost-weighted budget estimate by cross-referencing utilization ticks against local token logs. it is:

- instrumentation for detecting relative changes over time
- not a billing calculator
- not guaranteed to match anthropic's internal allocation exactly

cost normalization assumes API pricing reflects internal weighting of token types. absolute values may be approximate. relative changes over time are the signal.

## what would constitute proof

if multiple independent users on the same tier observe a synchronized decrease in normalized budget within a narrow time window, without a corresponding change in API pricing, that would provide evidence of a limit adjustment. ccmeter is designed to capture exactly this.
