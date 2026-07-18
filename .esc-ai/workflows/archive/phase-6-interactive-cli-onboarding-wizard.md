# Phase 6 — Interactive CLI Onboarding Wizard
**Status:** Complete
**Plan:** [`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md) (Phase 6)
**Spans:** esc-ai-orchestrator only — wraps already-tested esc-ai-execution-framework
onboarding logic, no changes there.

## Objective

A single `escape-ai` command a user can run to onboard a representative new
repository, from path to approved, commit-ready files. Only the "Onboard a
repository" menu item is functional this phase — Plan/Resume/Observe/Configure/
Validate are later phases and say so honestly rather than faking behavior.

## Deliverables

- [x] New `esc_orchestrator/escape_ai_cli.py`, a new `[project.scripts]` entry
      (`escape-ai`), separate from `esc-orchestrator` (which stays the HTTP daemon
      entry point, untouched). `escape-ai` does not require a running server — it
      instantiates `Store` and calls `esc_exec.onboarding` functions in-process, the
      same way the test suite already does.
- [x] Business logic stays out of the rendering layer: `render_menu`/
      `render_proposal`/`render_apply_result`/`render_status`/`render_validation` are
      pure functions over plain data, tested independently of real onboarding logic;
      `resolve_repository`/`analyze`/`apply_answers`/`repository_status`/
      `validate_all` are the only functions that touch `esc_exec`/`Store`, and are
      the same functions both the interactive and non-interactive paths call.
- [x] Non-interactive subcommands: `repository add/analyze/answer/apply/validate/
      status`. `answer` and `apply` are genuinely separate steps (not the same
      action under two names) — `answer` stages answers in a new
      `onboarding_pending_answers` Store table without writing anything; `apply`
      is the explicit, deliberate write step. `analyze` is always safe to re-run
      (read-only).
- [x] Resumption: starting onboarding again for a repository with an unchanged
      `input_digest` detects the existing stored proposal and, if already applied,
      shows the prior result and asks before re-running rather than silently
      re-analyzing and re-prompting.
- [x] Cancellation: Ctrl-C/EOF at any interactive prompt exits cleanly with "nothing
      was written" — the proposal (if analysis already ran) stays saved and
      resumable; nothing is ever half-applied.
- [x] Dry-run behavior: `analyze` (and the interactive proposal view before
      answering) is unambiguously a preview — "Nothing has been committed" is
      printed after every apply, and there is no code path where a proposal could
      be mistaken for something already written.

## Verification

16/16 tests pass (6 existing + 10 new: 5 pure rendering tests, 2 non-interactive
end-to-end tests against a real temp Gradle repository, 3 interactive end-to-end
tests with scripted `input()` against a real temp Gradle repository). Beyond the
test suite, manually ran the actual `escape-ai` module (not just its tests) against
two separate real temp Gradle repositories:

- Non-interactively: `add` → `analyze` → `answer` → `apply` → `validate` → `status`,
  confirming `esc-execution.yaml`, `content/esc-component.yaml`,
  `content/esc-verification-profile.yaml`, `content/esc-architecture-profile.yaml`,
  `INSTRUCTIONS.md`, and all three `.esc-ai/workflows/` files land on disk with the
  authored purpose text present, and `status` reports `has_applied_answers: True`.
- Interactively via piped `input()`: menu → path prompt → proposal render → purpose
  question → apply confirmation → same files written, confirmed on disk.

## Noted, not fixed (out of scope for this phase)

`repository validate` on a freshly-onboarded repository reports `esc-index.json` as
`STALE` and `esc-dependencies.json` as `INCOMPLETE`. This traces to
`apply_onboarding_answers` (esc-ai-execution-framework, Phase 4/5): it generates
indexes *before* generating the verification/architecture profiles that then get
added to each component's manifest, so the index is stale relative to the manifest's
final state the moment it's written, and the dependency graph is never generated at
all during apply. Not something to fix in this CLI-wrapping phase — flagging for
whoever next touches `apply_onboarding_answers`.
