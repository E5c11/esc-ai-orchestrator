# Escape AI — Vision

Status: draft, first written 2026-09-28. This is the thesis future implementation
should align to. When a ticket's framing and this document disagree, this document
wins — update it deliberately, don't let tickets silently drift away from it.

## What this is

Escape AI is a control plane for **deterministic, architecture-governed AI-assisted
software engineering** across existing repositories, over time. It is not a chat
interface to an AI coding assistant.

## The core bet

Commodity AI coding tools (Claude Code, Cursor, Codex, etc. used directly) optimize
for one thing: turn a prompt into a plausible diff, fast. That's a commodity now —
every serious provider does it. It is not what Escape sells.

Escape's bet is that the harder, more valuable problem is **repeatable, auditable
procedure** — the same class of change goes through the same gates every time,
regardless of who's asking or which provider is doing the work, and a human can trust
the result without re-reading every line, because the gates did their job. The value
is trust and consistency at scale, not speed of first output.

Concretely, this already exists and is not aspirational:

- Architecture coverage is checked against a real, resolvable framework
  (`esc-ai-architecture-framework`), not invented per-task.
- Verification is a real 4-stage progressive gate (`focused -> component -> impact ->
  final`) that runs actual subprocesses and stops at the first failure. The scheduler
  trusts the gate's exit code, never the agent's own claim of success.
- Execution defaults to a disposable worktree, not the live checkout.
- Failed runs are retained as durable, resumable checkpoints, not lost context.

## The procedure contract

Every unit of work is a **work type** (`fix`, `feature`, `plan`, `investigate`,
`refactor`, `document`, `job`, ...) mapped to a fixed, ordered sequence of **stages**
drawn from a small shared vocabulary (routing, scope declaration, architecture gate,
root-cause capture, baseline capture, planning, implementation, verification,
grounding check, report). Work types differ in *which stages compose their
procedure*, not in whether stages can be skipped ad hoc.

Two properties are non-negotiable:

- **A stage's presence in a procedure never depends on who is running it.** Whether
  `investigate` has no implementation stage at all, or `fix` requires root-cause
  capture before a plan can be produced, is a property of the work type, fixed for
  everyone.
- **`verify` and `architecture_gate` are never optional and never silently
  auto-passed.** They may run in different *interaction modes* (see below), but they
  always run.

The CLI's public verbs (see the intent-based CLI work) are a front door onto this
contract, not a replacement for it. A verb that routes to a procedure with no real
gates behind it is cosmetic, not a feature.

## Roles: interaction preference, not gate-skipping

Two independent knobs, not four hardcoded personas:

- `autonomy_preference` — ask-mostly / mixed / auto-mostly. Controls whether a
  *variable*-interaction stage (e.g. `architecture_gate`) is surfaced as a question
  or resolved automatically on the user's behalf.
- `explanation_depth` — minimal / standard / teaching. Controls how much of the
  *already-computed* context (resolved architecture docs, root-cause reasoning,
  verification detail) is surfaced in the report. This is a rendering concern, not a
  new computation — the data already exists by the time `report` runs.

Named presets (e.g. "junior dev," "vibe coder," "senior dev," "contributor") are
convenience mappings onto these two knobs for onboarding, not separate code paths.
Nothing in the execution engine should branch on a persona name directly.

Two guardrails on top of this, both still unimplemented and worth treating as real
scope, not footnotes:

- **A severity floor independent of `autonomy_preference`, and it's dynamic, not
  fixed.** Auto-resolving `architecture_gate` is only safe when the objective is
  covered by the *already-resolved framework profile* — applying a vetted pattern
  isn't really a judgment call, it's execution. The real risk concentrates entirely
  in the `local` case (see below): an unrated or thin-track-record local decision
  always surfaces regardless of how auto-leaning the user's preference is; a local
  decision with real accumulated evidence behind it earns auto-apply trust. The
  floor moves with the evidence, it isn't a static allowlist of "sensitive topics."
- **Persistence is gate-specific, not one generic mechanism.** `objective_gate`
  decisions (scope_boundary, completion_conditions — product shape) belong in a
  repository's `roadmap.yaml` `durable_decisions`. `architecture_gate` decisions
  have a better-fitting, already-existing mechanism: see "local architecture
  learning loop" below. Don't conflate the two just because both are
  "auto-resolved decisions" — they have different trust and reuse properties.

## Local architecture learning loop

When `architecture_gate` can't resolve an objective against the already-established
framework profile, the existing `write_local_architecture_note` mechanism labels it
`local` — unreviewed, repository-scoped. That label is where trust actually needs to
be earned, and the loop that earns it:

1. **Rate on outcome, primarily via architecture fitness, not raw test results.**
   `esc-ai-execution-framework`'s `architecture check` fitness functions measure
   conformance directly; test pass/fail and coverage are a secondary signal. A
   failing run doesn't always mean the *pattern* was wrong — it could be an
   unrelated bug or a poor implementation of a sound pattern. Don't conflate the two.
2. **Accumulate before trusting, either direction.** A rating is built from repeated
   applications of the same local note, not a single run — one bad run shouldn't
   permanently condemn a decent pattern, one lucky run shouldn't fast-track a bad
   one into auto-apply territory.
3. **No default submission of gaps to Escape itself.** This is a new, unproven
   product — asking users to send their architecture gaps off for central analysis
   asks for a trust that hasn't been earned yet (same principle as BLA-46's
   explicit-opt-in stance on rich trace sharing, applied here too).
4. **The credibility-building path is a real PR, not telemetry.** A local note with
   a strong accumulated track record becomes eligible (senior dev / contributor tier)
   to submit as an attributed pull request against `esc-ai-architecture-framework`
   itself, carrying its fitness/verification evidence as the PR's justification —
   reviewed like any ordinary OSS contribution. This is meaningfully different from
   BLA-46: BLA-46 is low-trust-bar aggregate operational telemetry; this is an
   explicit, evidence-backed, attributed contribution a human reviews. It's also
   only a credible flow because the framework repo is real Apache-2.0 now (BLA-41) —
   external contribution has actual legal footing, not just a suggestion box.

## What this is not

- Not a general-purpose agent framework where the model decides the process.
- Not "skip the gate to go faster" — `job` (the generic/freeform work type) runs the
  same gates as everything else; it only relaxes how loosely the objective is stated
  going in.
- Not persona-specific logic scattered through the codebase — role hooks live at a
  small number of seams (`variable`-interaction stages, report rendering), not as
  conditionals sprinkled wherever it's convenient.

## Repo boundaries

Three repositories, one product:

- `esc-ai-architecture-framework` — the knowledge: engineering principles, patterns,
  layer contracts. Reusable by any consuming repository, not private to the
  orchestrator.
- `esc-ai-execution-framework` — the engine: manifests, indexing, the procedure
  contract, verification, checkpoints. Provider-agnostic.
- `esc-ai-orchestrator` — the control plane: scheduling, the CLI/MCP surface,
  policy, roadmap persistence.

The three-repo boundary is a maintainer-facing decision, not a user-facing one. A
newcomer installing and running Escape AI should never need to know it's three
repositories — see the installation/onboarding work.

## Working note

This document should be read before scoping new tickets, and updated (not
contradicted in a ticket description) when the thesis itself changes.
