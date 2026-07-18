# Incompatible-Repository Migration Workflows — Plan

**Status:** Proposed
**Date:** 2026-07-18
**Objective:** For a repository that fails the onboarding readiness bar, offer a
tracked path toward compliance instead of just a diagnostic and a dead end — without
pretending to solve problems that aren't guided-migration problems at all.

## Why this is a separate plan

Same reasoning as the other standalone plans in this directory: a distinct concern
from the main plan's phase sequence, worth its own document rather than folding in
half-formed. General practice: capture a substantial new idea as its own plan doc as
it comes up, so it doesn't get lost or dilute whichever phase is active at the time.

## The idea

Right now, per the main plan's "Who this is for" section, a repository whose
`analyze_repository` proposal comes back dominated by `update`/conflict entries
(rather than clean `create`/`preserve` ones) is diagnosed as a poor fit and onboarding
declines. That's the honest response, but it's also a dead end — the human is told
"this doesn't work" with no tracked path forward.

The alternative: treat "get this repository to a compatible state" as a workflow (or
series of workflows) like any other — a `task.yaml` with an objective and completion
conditions, executed with the same bounded, verified, human-in-the-loop discipline as
a feature or fix. The completion condition is concrete and already exists: re-running
`analyze_repository` and getting a proposal dominated by clean entries instead of
conflicts. Once that bar is met, the rest of the system — planning, execution — simply
becomes available, the same way it already implicitly requires a ready repository
today. No separate "unlock" mechanism is needed; readiness already gates it.

This reuses what's already built rather than inventing a new one:

- The readiness signal (Phase 3's create/update/preserve/conflict classification).
- The task/checkpoint/verification-gate execution model.
- The migration-diff concept already named in the main plan's onboarding flow
  ("Existing workflow content is migrated through a reviewed proposal, never
  overwritten wholesale") but deferred out of Phase 5's actual scope.

This plan is a generalization of that deferred piece into something staged and
trackable, not a new concept sitting beside it.

## What this is not

**Not every kind of incompatibility is a guided-migration problem.** Two very
different categories hide behind "the readiness check failed":

- **Tractable**: missing purpose/architecture metadata, an out-of-date but
  structurally-similar manifest, conflicting-but-reconcilable existing docs. These
  are close to what the semantic-question flow already does (Phase 3/4) — a bounded,
  answerable gap.
- **Not tractable as a guided migration**: an unsupported build system, or a
  repository with no real module boundaries to declare as components. These require
  actual software architecture work — potentially months of it — and no tool should
  present that as a wizard-guided checklist. A migration workflow that overreaches
  here reintroduces exactly the failure mode "Who this is for" exists to prevent:
  promising to help with a messy repository and producing a worse result than a human
  would have, instead of being honest that this case needs a human to scope it first.

Any real design has to draw this line explicitly, and probably needs the migration
workflow itself to be able to conclude "this isn't a migration-workflow problem,"
not just eventually succeed or silently hang.

## Non-goals

- Do not design the actual taxonomy of migration workflows yet. What real
  incompatibility looks like — wrong build system vs. missing metadata vs. conflicting
  docs, and in what proportion — is exactly what Phase 10's pilot against a real
  repository will show. Speculating about it now is building ahead of evidence, the
  same mistake flagged on the templates and MCP ideas.
- Do not let a migration workflow attempt automated code restructuring (splitting a
  monolith into components, migrating build systems). It stays advisory and
  human-executed, with AI assistance per step, same as any other task — never "let
  the AI restructure your repository."
- Do not build this before Phase 5's deferred migration-diff proposal exists in its
  single-step form — this generalizes that piece; it doesn't replace the need to
  build it first.

## Open questions

1. Is a single long-running migration workflow the right shape, or a series of small
   ones (one per detected gap category), each independently completable?
2. Does the readiness check need a third outcome beyond ready/not-ready — something
   like "not a migration-workflow problem, needs human scoping first" — so the
   workflow itself can decline honestly rather than being started on a repository
   that can't actually reach compliance this way?
3. How much of this should be informed directly by Phase 10's `ampm-backend` pilot
   before any of it is designed further?
