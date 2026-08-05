# Architecture Guidance Reaching the Executing Agent — Plan

**Status:** Implemented (design sections 1-3, 5 -- real framework docs only)
**Date:** 2026-08-05
**Implemented:** 2026-08-05 -- `architecture_prompt_lines(component)`
(`esc-ai-execution-framework/esc_exec/architecture_lookup.py`) renders one read
instruction per resolved document, distinguishing stubs; wired into all three
adapters' `_prompt()` (`claude_code_adapter.py`, `codex_adapter.py`,
`opencode_adapter.py`) right after the existing per-component index-read line, same
insertion point and call shape as `roadmap_prompt_line`. No manifest/schema change --
purely reads data `build_task_context` already computed and previously discarded, so
every already-onboarded component benefits immediately. Parity tests added per
adapter (end-to-end through `.execute()`, asserting the doc path/id reach
`client.prompts[0]`, mirroring how the roadmap wiring was verified) plus unit tests
for the helper itself. Full suite green: esc-ai-execution-framework 511 -> 518.
**Design section 4 (local architecture notes) was deliberately not built in this
pass** -- per the design's own framing, a separate follow-on that first needs Open
question 1 (persistence: component-manifest field vs. task-scoped) resolved before
there's anything for a prompt-delivery fix to read. Local notes remain exactly where
they were: rendered into the task README only, never reaching the agent.
**Objective:** Close the gap where architecture-framework documents are resolved
against a component's `architecture.profile_ids`, gated on (execution hard-stops if
they're missing or still stubs), and written to `task-context.json` for provenance --
but never actually delivered into the prompt the executing coding agent receives.
Today the agent is told to read the component's own code index and search roots, and
nothing else; whatever guidance the framework says applies is computed, verified to
exist and be reviewed, then discarded before it reaches the agent doing the work.

## Why this is a separate plan

Surfaced while discussing a different feature (a guided architecture-recommendation
menu for new-project creation) and a question about whether escape-ai already
supports saving a local, per-repo architecture. Investigating the local-notes path
led to checking whether the *real* framework-resolved docs reach the prompt at all --
they don't, on a second, independent code path from the one already flagged in
[[active-design-plans]]'s "Important architectural finding" (the
`build_instruction_bundle` levels being provenance-only). That finding deliberately
left the fix as future work: "if a future session is asked to make architecture
docs/workflow policy actually reach the agent, touch `_prompt()` in all three
adapters." This is exactly that fix, now confirmed necessary via `task_context.json`
too, not just `instruction-bundle.json`.

Distinct from `plan/done/planning-consistency-checks.md` (local architecture notes
and the coverage-gap check) and `plan/future/framework-contribution-and-
extensibility.md` (promoting a local note to a real reviewed framework document) --
those are about *producing* guidance. This plan is about *delivering* guidance that
already exists and has already been verified to exist, to the one place that
currently never sees it: the agent's own instructions.

## What we found

- `build_task_context` (`esc-ai-execution-framework/esc_exec/task_context.py:58-94`)
  resolves each component's `architecture.profile_ids` into real framework documents
  via `resolve_architecture_docs` (transitive `requires` walk,
  `architecture_lookup.py:38-76`), and writes `entry["architecture"]` --
  `profile_ids`, `documents` (id/path/layer), `missing`, `stubs` -- into
  `task-context.json`.
- None of the three adapters' `_prompt()` methods reference this data at all:
  `claude_code_adapter.py:457-473`, `codex_adapter.py:274-289`,
  `opencode_adapter.py:170-185` are structurally identical and each only emit the
  objective, an optional roadmap line, tool/sandbox constraints, and per component
  `f"Then read {component['index']}...; search only: {component['search_roots']}."`
  (`claude_code_adapter.py:470`). `component["architecture"]` is never touched.
- A real hard gate does exist and does run:
  `_architecture_coverage_blockers` (`esc_orchestrator/runtime.py:58-66`) checks every
  component's fully-resolved `missing`/`stubs` and raises `ArchitectureCoverageError`
  (`runtime.py:18-43`, a `PreDispatchBlockerError`) at `runtime.py:117-122` --
  **before** `self.adapter.execute(...)` at `runtime.py:133`. `Scheduler._work`
  (`scheduler.py:196-202`) catches it and records a `"failed"` run without ever
  invoking the agent. So a task cannot run at all if its architecture guidance is
  missing or unreviewed -- but once that gate passes, the *content* still never
  reaches the prompt.
- Confirmed this is the only execution path: `ClaudeCodeAdapter.execute` (and its
  Codex/OpenCode equivalents) is only ever called from `_AdapterRuntime.execute`
  (`runtime.py:133`), reached identically from the interactive CLI, the HTTP daemon
  (`cli.py`), and headless `escape-ai task run --yes` (`escape_ai_cli.py`'s
  `execute_task` -> `Scheduler.submit`/`_work`). `BACKDOOR.md`'s headless flow does
  not bypass the gate or take a different route to the agent -- same code, same gap.
- Local architecture notes (`.esc-ai/local-architecture/*.md`, written by
  `esc_exec/local_architecture.py::write_local_architecture_note`) are further behind
  than the real framework docs: they're rendered only into the task's `README.md`
  (`planning.py:107,121-129`) as a deliberate, already-made decision (see
  `planning-consistency-checks.md`'s task-breakdown correction: "no
  `task-specification.schema.yaml` change needed... README-only"). They're not in
  `task-context.json` at all, so there's nothing today for a prompt-delivery fix to
  even read for local notes without first revisiting that persistence decision.
- Precedent for how to wire something into all three prompts already exists and
  works: `roadmap_prompt_line(repository)` (`esc_exec/roadmap.py:76-99`) returns
  `None`/skip-if-absent or one formatted line, called identically by all three
  adapters (`claude_code_adapter.py:460-462`, `codex_adapter.py:277-279`,
  `opencode_adapter.py:173-175`), verified with parity tests per adapter.

## Design

1. **New shared helper**, same shape as `roadmap_prompt_line`, e.g.
   `architecture_prompt_lines(component: dict) -> list[str]` (likely in
   `architecture_lookup.py`, next to `resolve_architecture_docs`/`stub_documents`
   since it operates on their output shape). For each resolved document in
   `component["architecture"]["documents"]`, emit a line telling the agent to read
   it by path -- matching the existing pattern of pointing at files
   (`component["index"]`) rather than inlining content, so the agent always reads
   the live file rather than a build-time snapshot that can go stale. Documents in
   `component["architecture"]["stubs"]` get a visibly different line (e.g.
   `"...(stub, unreviewed -- treat as a starting point, not a rule)."`) so the agent
   doesn't treat unreviewed guidance as settled. Return `[]` when a component has no
   `architecture` key at all (most components today, pre-onboarding-migration) --
   purely additive, no behavior change for anything without profile_ids.
2. **Wire it into all three `_prompt()`s**, one call per component, right after the
   existing `"Then read {component['index']}..."` line -- same insertion point and
   call shape as `roadmap_line`, so the three adapters stay structurally identical.
3. **No manifest or schema change needed for this part.** `profile_ids` and their
   resolution already exist and are already gated on; this is purely "stop
   discarding data that's already computed." Every already-onboarded repository
   benefits immediately, with zero re-onboarding.
4. **Local architecture notes, as a deliberate follow-on, not bundled into the same
   change:** give the component manifest a sibling field to
   `architecture.profile_ids` -- e.g. `architecture.local_note_paths` -- so
   `build_task_context`'s existing per-component resolution loop
   (`task_context.py:58-94`) picks local notes up the same way it already picks up
   `profile_ids`: durably, for every future task touching that component, not a
   one-off list attached to whichever task happened to trigger the coverage-gap
   offer. `architecture_prompt_lines` (or a sibling helper) renders these with a
   third, even more visibly-unreviewed marker than stub framework docs get, so the
   agent never conflates "framework-reviewed," "framework stub," and "local,
   unreviewed, written by the operator five minutes ago."
5. **Testing:** parity tests per adapter (all three, not assumed from one) proving
   the prompt actually contains the read instruction when `architecture.documents`
   is present, is silent when absent, and marks stubs/local notes distinctly --
   matching how the roadmap wiring was verified.

## Non-goals

- Not inlining full document content into the prompt. Pointing at a path to Read
  matches the existing, working pattern for component indexes and keeps the agent
  reading live files, not a build-time copy.
- Not changing `_architecture_coverage_blockers` or the hard-stop gate itself --
  it's already correct and already protects every run; this plan only changes what
  happens with the content once the gate has already passed.
- Not building the local-notes promotion-to-framework flow -- that's
  `framework-contribution-and-extensibility.md`'s job.
- Not building the guided architecture-recommendation menu for new-project creation
  discussed alongside this (separate feature idea, not yet its own plan doc) --
  this plan is an execution-time prerequisite for that idea eventually mattering,
  not the idea itself.
- Not retroactively re-running or flagging past task runs that executed without
  doc content in-prompt -- an operator-facing disclosure question, not a code
  change; not addressed here.

## Open questions

1. **Local-notes persistence: component-manifest field vs. task-scoped field.**
   This plan's design section 4 leans component-manifest (durable, matches
   `profile_ids`'s own pattern), which reopens a decision `planning-consistency-
   checks.md` deliberately made the other way (README-only, no schema change) --
   worth re-confirming intentionally rather than drifting into it, since that
   earlier doc's own task breakdown explicitly corrected an assumption in the
   opposite direction once.
2. **Exact three-tier prompt wording** (framework-active / framework-stub /
   local-unreviewed) -- left for implementation-time judgment, but the three tiers
   must read as unambiguously different levels of trust to the agent, not just
   different labels.
3. Whether local notes should resolve into `entry["architecture"]["documents"]`
   itself (merged, tagged by source) or stay a visibly separate list end-to-end
   (`entry["architecture"]["local_notes"]`) -- the existing task-doc precedent
   (`local_architecture_notes` kept distinct from `architecture_doc_ids`, never
   merged) leans toward staying separate; worth confirming against that precedent
   rather than re-deciding from scratch.
4. Whether `build_instruction_bundle`'s matching `architecture_framework_core_and_
   profile` level (still provenance-only per the earlier finding) should be fixed
   in the same pass, since it's the same shape of gap on a different artifact --
   or left alone as genuinely out of scope since nothing reads it today for any
   purpose this plan touches.
