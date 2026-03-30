# Evidence: Anthropic's pattern of opaque limit changes

## Incident 1: Christmas 2x promo → post-holiday nerf (Dec 25, 2025 – Jan 5, 2026)

Anthropic ran a 2x usage promotion from December 25 at 00:00 UTC through December 31, covering Pro and Max subscribers across claude.ai, Claude Code, and Claude in Chrome.

When it expired, limits felt tighter than pre-promotion baseline. A Claude Code user provided The Register with screenshots showing roughly 60% reduction in token usage limits based on token-level analysis of Claude Code logs. Anthropic's response: users are "reacting to the withdrawal of bonus usage awarded over the holidays."

A Max plan subscriber who'd never hit limits in three months [filed a bug report](https://github.com/anthropics/claude-code/issues/16157) on January 3 — hitting usage limits after two hours of normal usage. The issue remains open.

On forums, Reddit, and the Claude Developers Discord, developers reported token consumption suddenly increasing, with accounts reaching maximum within minutes or hours on tasks that previously worked fine.

## Incident 2: March 2x off-peak promo → simultaneous silent nerf (Mar 13–28, 2026)

From March 13 through March 28, five-hour usage was doubled during off-peak hours (outside 8 AM–2 PM ET on weekdays). Framed as a thank-you for growth after a competitor boycott drove Claude to #1 on the App Store.

During the same promotional period, Anthropic silently tightened peak-hour session limits. Reports started around March 23, with a $200 Max subscriber posting screenshots showing session usage climbing from 52% to 68% to 91% within minutes of a five-hour window.

The explanation came days later — not as a blog post, but a tweet thread from Thariq, one engineer. About 7% of users would now hit limits they wouldn't have before. Pro subscribers most affected.

## The pattern

Same structure both times:

1. Announce generous temporary promotion
2. Silently tighten baseline limits during or immediately after the promotion window
3. Attribute complaints to "contrast effect" or "return to normal"

Meanwhile: Anthropic does not publish token budgets, does not version cap announcements, and does not provide a changelog when limits change. The usage API reports percentages at 1% granularity — a percentage of an undisclosed number.

## Why this matters

This isn't about the limits being too low. It's about the opacity.

If you're paying $200/month for Max 20x, you should be able to answer: "20x what?" Anthropic doesn't say. The only way to find out is to measure it yourself — which is what ccmeter does.
