# Reintroduce Gemini Provider — Plan

**Status:** Proposed
**Date:** 2026-07-19
**Objective:** Bring `gemini` back as a selectable provider once there's a real,
verified path to it, and record exactly what was removed and why so it isn't
rediscovered the hard way.

## Why this is a separate plan

Same reasoning as the other standalone plans in this directory: this is a distinct,
trackable follow-up action (a deliberate removal with an explicit intent to revisit),
not something to leave as a buried code comment. Directly follows from the Antigravity
findings already recorded in `native-cli-provider-adapters.md` open question 2.

## What was removed and why

`gemini` was pulled from `KNOWN_PROVIDERS` (`esc_exec/registry.py`) on 2026-07-19 --
removed from the provider picker entirely, both interactive (`prompt_provider_setup_
interactive`) and non-interactive (`escape-ai provider auth gemini` now rejected as an
unknown provider, not just an unsupported route).

This was **not** because nothing works for gemini. Antigravity's (`agy`) subscription
billing is confirmed real -- a live quota-exhaustion error explicitly referenced
"upgrade your subscription," and a second, unpaid account also drew from real quota
without falling back to API-key billing. The actual blocker is reliability: three
independent live attempts (`--mode plan`, default mode, `--dangerously-skip-
permissions`) all failed to execute the literal given prompt at all -- `agy --print`
consistently investigated its own CLI flags/config instead and answered a meta-
question about itself. That's a real, reproducible finding, not a fluke.

Rather than leave a provider option in the picker that doesn't reliably do the one
thing it's offered for, it was removed outright until that's resolved.

## Important nuance -- read before touching this

Removing `gemini` from `KNOWN_PROVIDERS` entirely also removed its **OpenCode
API-key route** -- which has nothing to do with Antigravity's reliability problem.
OpenCode's Gemini support is a separate, mature, already-working path (OpenCode
already implements 75+ providers, gemini among them). That route got removed too,
purely as a side effect of removing the provider wholesale for picker simplicity
("nothing offered doesn't work"), not because the API-key route itself is broken.

This is a **deliberate trade** -- simplicity of the picker over completeness of what's
technically available -- not a technical necessity. Worth remembering if someone wants
Gemini-via-API-key back sooner than a real Antigravity fix.

## Two independent paths back in

1. **Antigravity subscription route** -- blocked on the actual reliability finding,
   not the billing question (already resolved, see above). Needs either a newer `agy`
   version verified live to actually execute a given headless prompt correctly, or a
   root-caused understanding of why it investigates its own flags instead of running
   the real prompt.
2. **Gemini via OpenCode API-key route** -- no technical blocker at all. Could be
   reintroduced independently and immediately by re-adding `"gemini"` to
   `KNOWN_PROVIDERS` without touching `SUBSCRIPTION_CAPABLE_PROVIDERS` (mirroring
   exactly how it worked before this removal: known provider, api-key-only,
   `prompt_provider_setup_interactive` skips the route sub-prompt and routes straight
   through OpenCode). Was removed only as a side effect of the picker-simplicity
   decision above, not because it doesn't work.

## Non-goals

- Do not re-add gemini's subscription route without a genuine live re-verification of
  `agy`'s execution reliability specifically -- the billing question alone was already
  resolved before this removal and was never the blocker.
- Do not silently re-add gemini as part of an unrelated change -- this should be a
  deliberate, reviewed decision each time (see the two independent paths above), not
  a side effect.

## Open questions

1. Should path 2 (API-key route) be reintroduced now, independent of path 1 (which
   depends on someone else's software getting more reliable)? Not decided -- deferred
   along with the removal itself, since the user's explicit call was to remove the
   option now and revisit later, not to immediately re-add a partial version of it.
2. When path 1 is revisited, what's the actual re-verification bar -- one more clean
   live run, or a small number across different prompts/repos to rule out the
   meta-question failure mode being prompt-shape-dependent rather than universal?
