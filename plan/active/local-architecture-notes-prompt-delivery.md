# Local Architecture Notes Reaching the Executing Agent — Plan

**Status:** Proposed
**Date:** 2026-08-05
**Objective:** Make local architecture notes (`.esc-ai/local-architecture/*.md`,
written by `esc_exec/local_architecture.py::write_local_architecture_note`) durably
resolve into the AI prompt via the same delivery mechanism
`plan/done/architecture-guidance-prompt-delivery.md` just built for real
framework docs -- not just the task `README.md`, which is all they reach today.

## Why this is a separate plan

`architecture-guidance-prompt-delivery.md`'s design section 4 named this exact
follow-on and deliberately deferred it, pending its own Open question 1
(persistence: component-manifest field vs. task-scoped field). This plan resolves
that question with a concrete design, grounded in code this round's investigation
read directly rather than assumed from the earlier plan's lean.

## What we found

- `offer_local_architecture_note_interactive` (`esc-ai-orchestrator/esc_orchestrator/
  escape_ai_cli.py:1476-1526`) checks coverage **aggregated across every component
  in the task's `components` list at once** (`architecture_doc_ids_for_components`,
  `esc-ai-execution-framework/esc_exec/planning.py:91`) and drafts at most one note
  per call. A note is not intrinsically owned by a single component -- the earlier
  plan's "component-manifest field" lean needs a concrete answer for *which*
  component(s) get the pointer, not just "the manifest."
- `write_local_architecture_note` always writes `status: "stub"`
  (`local_architecture.py:52`) -- local notes are permanently, definitionally
  unreviewed; there's no local promotion path (only
  `framework-contribution-and-extensibility.md`'s separate, human-driven flow).
  They must never read as the same tier as a framework doc that's merely
  `status: stub` pending review -- that's a doc on its way to `active`; a local
  note has no such trajectory without a human explicitly promoting it elsewhere.
- `_architecture_coverage_blockers` (`esc-ai-orchestrator/esc_orchestrator/
  runtime.py:58-66`) only inspects `missing`/`stubs` under `component["architecture"]`.
  A new sibling key is invisible to it -- confirms a separate `local_notes` key
  automatically preserves the already-made "warn-and-proceed, never a hard gate"
  decision (`planning-consistency-checks.md`) with no new gate logic required, not
  just as a style preference.
- **Manifest regeneration already merges, not replaces** --
  `generate_gradle_manifests`/`generate_npm_manifests` (`manifests.py:270-339`)
  write via `_merge_generated(existing, generated)` (`manifests.py:63-70`), and the
  generator's own `generated` dict never contains an `architecture` key at all. So
  `existing["architecture"]` -- profile_ids today, and a new `local_note_paths`
  sibling -- survives every re-onboarding apply untouched, automatically, the same
  way profile_ids already does. Resolves what would otherwise be an open question.
- **Real blocker found, not hypothetical:** `_architecture_selector_errors`
  (`manifests.py:73-82`) hard-rejects any `architecture` mapping containing a key
  other than `profile_ids` (`"architecture must be a mapping with only a
  profile_ids key"`), called from both repository- and component-manifest
  validation (`manifests.py:421` and `:535`). Writing a `local_note_paths` field
  today would fail manifest validation immediately -- this validator must be
  updated as part of this plan, not an afterthought.
- Both `component-manifest.schema.yaml`'s `architecture` property
  (`additionalProperties: false`, only `profile_ids`, lines 46-53) and
  `repository-manifest.schema.yaml`'s `$defs.architecture` (structurally
  identical, not a shared `$ref`, lines 60-67) need the same new sibling field.
- `task-context.schema.json`'s per-component `architecture` object
  (lines 39-61) currently `"required": ["profile_ids", "documents"]` whenever
  `architecture` is present at all. A component with local notes but genuinely no
  matching framework profile_ids (a real, likely-common case -- local notes exist
  precisely because framework coverage is thin) would violate that today. Needs
  loosening to make `profile_ids`/`documents` optional.

## Design

1. **Schema:** add `local_note_paths: array[string]` alongside `profile_ids` in
   both `component-manifest.schema.yaml` and `repository-manifest.schema.yaml`'s
   architecture definitions. Update `_architecture_selector_errors`
   (`manifests.py:73-82`) to accept `{"profile_ids", "local_note_paths"}` as the
   allowed key set, and to validate `local_note_paths` the same shape as
   `profile_ids` (non-empty list of non-empty strings) when present. Loosen
   `task-context.schema.json`'s per-component `architecture.required` to `[]` --
   all of `profile_ids`/`documents`/`missing`/`stubs`/`local_notes` become
   independently optional.
2. **`task_context.py`:** add `_local_note_paths(manifest)` mirroring
   `_profile_ids`. Merge repository-level + component-level paths the same way
   `profile_ids` merges (`list(dict.fromkeys(repository_... + component_...))`,
   deduped). For each resolved path, read its frontmatter via the existing
   `read_local_architecture_note_frontmatter` to recover its `id`; populate
   `entry["architecture"]["local_notes"] = [{"id":..., "path":...}, ...]`, kept
   **structurally separate from `documents`** -- both because a local note isn't a
   framework-index entry (no `requires` chain to walk, no `layer` in the
   meaningful sense) and because keeping it out of `stubs` is what keeps the
   hard-stop gate blind to it (see "What we found"). Create/extend
   `entry["architecture"]` whenever *either* `profile_ids` or `local_note_paths`
   is non-empty -- today it's gated solely on `profile_ids`.
3. **`architecture_lookup.py`:** extend `architecture_prompt_lines` with a third,
   visibly distinct tier for `architecture.get("local_notes", [])` -- e.g. `f"Also
   read {path} ({id}) -- a local, unreviewed note written by this repository's
   operator, not a reviewed framework document; treat with extra caution and
   verify independently where practical."` Must read as unambiguously less
   trusted than even a framework stub doc, not just differently worded.
4. **`offer_local_architecture_note_interactive`
   (`esc-ai-orchestrator/escape_ai_cli.py`):** after `write_local_architecture_note`
   succeeds, for every `component_id` in the `components` list passed into this
   call, load that component's manifest, append the note's repository-relative
   path into `architecture.local_note_paths` (dedup, preserve whatever's already
   there), write it back. This is the actual persistence step -- it's what makes
   the note durably visible to *every future task* touching any of those
   components, not just the one whose planning conversation happened to trigger
   the gap. Leave the existing README-rendering path in `planning.py` exactly as
   it is -- still useful, human-facing, and harmless to keep alongside the new
   mechanism the agent actually reads from.
5. **Tests:** `test_task_context.py` cases mirroring the existing
   `test_missing_and_stub_architecture_documents_are_reported` shape, for
   local-notes-only components (no profile_ids at all) and merged
   repository+component paths; `test_architecture_lookup.py` cases for the third
   prompt tier; a `test_manifests.py` case proving a `local_note_paths`-bearing
   manifest now validates (and that an unrelated stray key still doesn't);
   an `escape_ai_cli` test proving `offer_local_architecture_note_interactive`
   writes to every named component's manifest, not just the note file; parity
   end-to-end prompt tests per adapter, matching the discipline the previous
   plan's tests already established.

## Non-goals

- Not changing `_architecture_coverage_blockers` -- local notes staying
  non-blocking is already guaranteed by design section 2's separate-key choice,
  not something this plan adds gate logic for.
- Not building any local-to-framework promotion mechanism -- still
  `framework-contribution-and-extensibility.md`'s job. This plan only makes a
  note's existing, still-unreviewed content actually reach the agent at the same
  trust level it's always had (stub-equivalent), not a higher one.
- Not touching `planning.py`'s README-rendering path -- stays as-is.
- Not resolving a local note's own `requires`/`related` frontmatter fields
  against the real framework index. They're fields on an unreviewed document;
  walking them the way `resolve_architecture_docs` does for real framework docs
  is unbuilt, separate scope (arguably only meaningful post-promotion), not
  assumed here.
- Not inventing single-owner semantics for a note referenced by multiple
  components -- each component in scope when the gap was found keeps its own
  manifest pointer to the same note path. Small, harmless duplication; simpler
  than an ownership model nothing else in this system has either (profile_ids
  has no ownership concept beyond "this component declares this ID" too).

## Open questions

1. Should a note ever be removed/pruned from a component's `local_note_paths`
   (promoted, judged stale)? No removal UI exists today for anything comparable
   in this system (`profile_ids` has none either) -- leaving this as a manual
   manifest hand-edit matches existing precedent, but worth confirming rather
   than assuming it's acceptable indefinitely.
2. Exact third-tier prompt wording -- left for implementation-time judgment, must
   stay unambiguous against both the active-framework-doc and stub-framework-doc
   tiers `architecture-guidance-prompt-delivery.md` already built.
3. Whether `escape-ai repository analyze`'s Tier 1/Tier 2 re-detection should
   ever *suggest* new local notes on its own (today only the planning-time
   coverage-gap conversation offers one) -- out of scope here; this plan only
   fixes delivery of notes however they get created.
