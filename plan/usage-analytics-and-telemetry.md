# Usage Analytics and Telemetry — Plan

**Status:** Proposed
**Date:** 2026-07-19
**Objective:** Let escape-ai (with explicit, opt-in consent) report aggregate usage and
cost data back to us, so we can understand how much running the tool actually costs
people and how they're using it.

## Why this is a separate plan

Same reasoning as the other standalone plans in this directory: a distinct concern from
the main plan's phase sequence, worth its own document so it isn't lost. Surfaced
directly out of building the Claude Code adapter — `claude -p`'s `result` message
already reports a real `total_cost_usd` and a `rate_limit_event` stream
(`rateLimitType`, `resetsAt`, `overageStatus`, `isUsingOverage`) per run, which is
exactly the kind of data an opt-in analytics feature would aggregate. See
`native-cli-provider-adapters.md`'s open question 4 (per-user usage/cost visibility)
and `task-orchestration-and-verification-loop.md`'s 90%-usage dispatch pause — both are
the *local, single-user* facing side of this; this plan is the *aggregate, opt-in,
sent-back-to-us* side, a genuinely different concern.

## The idea

- An explicit "allow analytics" opt-in (default off) — asked once, alongside the other
  first-run/Configure system questions (see `native-cli-provider-adapters.md`'s open
  question 5), never assumed.
- If enabled, escape-ai reports aggregate cost/usage/token data (the same shape
  `run-metrics.json` and the provider's own `total_cost_usd`/rate-limit fields already
  produce locally, per run) back to us, so we can see real-world cost and usage
  patterns across users rather than guessing.
- Bounded and observable, same discipline as everything else in this system: report a
  usage record, never raw prompts/task content/repository data. What gets sent should
  be as inspectable as any other artifact this system produces.

## Non-goals

- Do not build this now. Explicitly deferred — this is a plan to think from later, not
  a queue to implement from yet.
- Do not make analytics opt-out or silently default-on. Consent is a first-run
  question with a real "no," not a buried setting.
- Do not send anything beyond aggregate cost/usage/token shape data — no prompt
  content, no repository contents, no task objectives.

## Open questions

1. Where does reported data actually go (a hosted endpoint we run, or something
   simpler for a v0)? Not investigated.
2. Exact shape of the reported record — presumably close to `run-metrics.json` plus
   the provider's own cost/rate-limit fields, but not designed.
3. Per-provider consent, or one global switch? (Mirrors the same per-provider-not-
   global question already resolved for auth in `native-cli-provider-adapters.md`
   open question 5 — likely the same answer, not confirmed.)
