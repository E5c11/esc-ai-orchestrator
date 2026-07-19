# Build the Garage Fault-Triage Exercise Through escape-ai — Plan

**Status:** Active
**Date:** 2026-07-20
**Objective:** Use escape-ai itself — onboarding, planning (including the AI
conversation primitive), and task execution — to build a small take-home-style
exercise: a Next.js app that turns a customer's free-text vehicle fault description
into something structured and useful for a service adviser (2 screens: customer
submission, manager/adviser dashboard). The point is deliberate: showcase a tool
built for AI-native software delivery, used to build a real AI-native feature
end to end, not just described in the abstract.

## Why this is a separate plan

Depends on `plan/active/npm-build-system-support.md` (the general onboarding capability) and
exercises `plan/done/ai-conversation-primitive.md`'s planning consumer and
`plan/active/scaffold-new-or-empty-repository.md`'s resolved wizard-first direction, but is
itself a specific project with its own scope and sequencing — not a generic
capability. Same "distinct concern, own doc" reasoning as every other plan here;
folding this into the npm-adapter doc would make that doc unreusable for any other
future npm project, and folding it into the conversation-primitive doc would repeat
the mistake `scaffold-new-or-empty-repository.md` already made once (see that doc's
now-corrected "Two consumers" history in `ai-conversation-primitive.md`).

## Prerequisite

`plan/active/npm-build-system-support.md` must be built first — this plan does not re-design
that capability, it consumes it. Do not start task 5 below before that plan's scope
is done.

## Scope / sequence

1. **Scaffold externally, not through escape-ai** — `npx create-next-app@latest`,
   per `scaffold-new-or-empty-repository.md`'s resolved direction (escape-ai points
   at the wizard, doesn't reinvent it). Two-screen skeleton: a customer-facing
   submission route and a manager/adviser dashboard route.
2. **Onboard the scaffolded repo through escape-ai** — real exercise of
   `npm-build-system-support.md`'s new adapter, not a synthetic test fixture.
3. **Register `esc-ai-architecture-framework`** in the working registry.yaml
   (`frameworks` route) so `platforms/web/nextjs-app.md`'s layering conventions
   (Server Component shell -> Client Component -> hook -> Server Action, no
   speculative UseCase/Repository layers) actually reach the AI's instruction bundle
   during execution — this is the whole point of onboarding pointing at that
   framework in the first place, not incidental.
4. **Plan the feature through `run_planning_interactive`**, deliberately using
   `run_planning_conversation_interactive` (the AI conversation primitive) to refine
   scope — this is that primitive's first real dogfooding beyond its live smoke test.
   The actual design decisions (data shape for a parsed complaint, category/urgency
   taxonomy, whether persistence is a module-level in-memory store or something else)
   belong in that conversation, not pre-decided in this plan doc.
5. **Execute the resulting task(s)** through `ClaudeCodeRuntime`/`ClaudeCodeAdapter` —
   real code-writing via escape-ai's execution path, not the assistant editing files
   directly outside the tool.
6. **Review and iterate** — apply checkpoints/resume as needed if a task doesn't
   converge in one execution pass, exercising that existing machinery too.

## Stretch (optional, not required for 1-6 to be a complete showcase)

**Real verification-gate execution.** `task-orchestration-and-verification-loop.md`'s
gate-execution work has never been started, for any language. "The AI wrote the code
and I independently verified it actually builds and passes its tests" is a
meaningfully stronger showcase than trusting the agent's self-report — which is
exactly this whole system's stated philosophy ("trust the artifact, not the agent")
being demonstrated on itself, not just built for hypothetical future use. Worth
strong consideration, but explicitly not a blocker: phases 1-6 stand on their own as
a real demonstration even if this stretch isn't reached.

## Non-goals

- Do not pre-design the take-home app's actual content here (fault taxonomy, exact
  data shape, UI details) — that belongs in step 4's planning conversation, where
  this system's tools are meant to produce it, not in this roadmap doc.
- Do not block on `task-orchestration-and-verification-loop.md`'s full 9-item
  breakdown — only the stretch goal above touches that surface, and only a minimal
  slice of it (running one build/test gate for real) if pursued at all.
- Do not treat this as validation that escape-ai is generally ready for small
  greenfield projects — it demonstrates one specific path made to work, not a general
  claim; see the `escape-ai-scope-and-gaps` memory for the broader, still-accurate
  scope conclusion.

## Task breakdown (ordered by dependency)

1. Complete `plan/active/npm-build-system-support.md` in full (its own task list applies)
2. `npx create-next-app` the actual app skeleton locally — manual step, not code
   escape-ai writes
3. Register the new repo + `esc-ai-architecture-framework` in a working registry.yaml
4. Onboard the repo through escape-ai (`repository add` / `analyze` / `apply`)
5. Plan the feature via `run_planning_interactive`, exercising the AI conversation
   step for real
6. Execute the task(s), review the result, iterate via checkpoint/resume if needed
7. Write up the showcase — what escape-ai did automatically vs. what still needed
   manual intervention, honestly, since that honesty is itself part of what's worth
   showing
8. (Stretch, optional) minimal real verification-gate execution for this repo

## Open questions

1. Timing — is this meant to actually be submitted as a real take-home deliverable
   under its own time constraint, or built afterward/separately purely as a showcase
   once the tooling supports it? Not stated yet; determines how much of task 1's
   scope is affordable before starting task 2 versus needing to run in parallel or be
   descoped further.
2. Does the showcase write-up (task 7) live in this repo's `plan/` directory, in the
   new take-home repo's own README, or both? Leaning both — a short pointer here,
   the real narrative in the take-home repo where an external reviewer would actually
   look. Not decided.
