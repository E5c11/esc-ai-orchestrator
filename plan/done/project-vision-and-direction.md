# Project Vision and Direction — Plan

**Status:** Implemented
**Date:** 2026-08-04
**Revised:** 2026-08-04 — the original version of this doc designed a brand-new
`.esc-ai/vision.md` artifact from scratch. Investigation found this duplicates an
already-built, already-dogfooded mechanism (`project_roadmap`, `.esc-ai/roadmap.yaml`,
from `plan/done/ai-conversation-primitive.md`) that covers the same concept more
completely than the new artifact would have. This revision closes that mechanism's
three real gaps instead of building a parallel one.
**Implemented:** 2026-08-05 -- all three designs shipped: (1)
`escape-ai roadmap show/set` plus "Configure system" -> "Show / set project
roadmap" (`configure_roadmap_interactive`), independent of the AI conversation;
(2) a new shared `roadmap_prompt_line` (esc-ai-execution-framework
`esc_exec/roadmap.py`) wired into all three adapters' real `_prompt()`
(`claude_code_adapter.py`, `opencode_adapter.py`, `codex_adapter.py`), plus a
provenance-only entry in `build_instruction_bundle`'s
`repository_instructions_and_workflow_policy` bucket; (3)
`run_planning_conversation_interactive`'s confirm step now prints current vs.
proposed roadmap before asking, resolving `ai-conversation-primitive.md` open
question 7. Full suites green in both repos (511 tests, esc-ai-execution-
framework; 236 tests, esc-ai-orchestrator), including live parity tests proving
a saved roadmap reaches the real prompt text for every adapter, not just the one
this was first built against.
**Objective:** Give `project_roadmap` (1) a direct human-authored read/write path
independent of the AI-mediated planning conversation that's currently the only way
to reach it, (2) a real effect on task execution, not just on the next planning
conversation's prompt, and (3) a visible diff before an update overwrites it.

## Why this is a separate plan

Distinct from `plan/done/ai-conversation-primitive.md` (Implemented): that plan
designed and built `project_roadmap`/`conversation_summary` as the durable-state
half of the AI-conversation primitive. This plan doesn't touch that design — it
closes three gaps the original plan's own "Open questions" and this session's
investigation surfaced, none of which needed a new artifact to fix.

## What we found

- `project_roadmap` (`esc_exec/roadmap.py`, esc-ai-execution-framework) already has
  exactly the shape the original version of this doc was about to reinvent:
  `purpose`, `current_stage`, `direction`, `durable_decisions` — one durable YAML
  document per repository (`.esc-ai/roadmap.yaml`), updated in place, never appended.
- It's read and written today by exactly one caller:
  `run_planning_conversation_interactive` (`escape_ai_cli.py:1690-1787`) — the
  "chat about it" AI conversation offered during single-repository plan drafting.
  Reading it needs no provider; **writing** it only ever happens as a side effect of
  a real AI conversation, which needs a connected Claude subscription, a
  single-repository plan, and the human opting in. **There is no way to view or set
  it directly** — no CLI command, no "Configure system" entry, nothing. A user who
  wants to just state direction without holding a conversation about it has no path
  in.
- **It is never read at execution time.** Confirmed by grepping every adapter's
  `_prompt()` (`claude_code_adapter.py:456`, `opencode_adapter.py:169`,
  `codex_adapter.py:273`) — each builds the real text sent to the agent from
  objective/tool-constraints/components/index paths only. None reference
  `roadmap.py`, architecture documents, or workflow policy at all. Separately,
  `build_instruction_bundle` (`instructions.py`) composes a precedence-ordered list
  of source *labels* (e.g. `.esc-ai/workflows/README.md`) written to
  `instruction-bundle.json` — but this is a provenance/audit artifact only, never
  read back into any adapter's prompt (matches a gap already flagged in
  `manifest-testing-facts-and-documentation-obligation.md`: "`build_instruction_
  bundle` isn't actually read by any adapter prompt"). So even the original design's
  plan to "wire vision into the instruction bundle at precedence level 4" would have
  landed in the same unread JSON file `project_roadmap` would join today — a
  cosmetic fix, not a functional one. The actual gap is that **no adapter's real
  prompt text includes repository-level durable context at all**, and closing that
  requires touching each adapter's `_prompt()`, not the bundle.
- `ai-conversation-primitive.md`'s own open question 7 is still unresolved: the
  `confirm("Update this repository's saved roadmap...")` prompt
  (`escape_ai_cli.py:1777`) shows nothing about what's changing before asking —
  neither the current values nor the proposed new ones.

## Design

### 1. Direct read/write path, independent of the AI conversation

- `escape-ai roadmap show <repository-id>` — prints the current `project_roadmap`
  (purpose/current_stage/direction/durable_decisions/updated_at), or "No roadmap set
  yet for this repository" if none exists — never an error.
- `escape-ai roadmap set <repository-id> <answers.json>` — `{"purpose": ...,
  "current_stage": ..., "direction": ..., "durable_decisions": [...]}`; a field
  omitted from the JSON keeps its current saved value rather than being blanked, so
  a caller can update just one field without restating everything.
- Interactive: a new action in "Configure system" (`run_configure_interactive`,
  `escape_ai_cli.py:2068`) — "Show / set project roadmap" — reuses the existing
  repository picker pattern (`registered_repository_ids` + `select_menu`, same as
  "Observe a run"), then a small Q&A pre-filled with current values (blank input
  keeps the existing value, same convention onboarding's suggestion-acceptance
  already uses), not a live AI conversation. This is a genuinely separate, simpler
  entry point from `run_planning_conversation_interactive` — it does not replace
  the AI-mediated path, it just stops requiring one.

### 2. A real effect on execution, not just the next conversation's prompt

- A new shared helper, `roadmap_prompt_line(repository: Path) -> str | None` in
  `esc_exec/roadmap.py` — renders the repository's `project_roadmap` (if any) as one
  prompt line (`None` if none saved, so a caller can skip it cleanly rather than
  emit a hollow placeholder).
- Each adapter's `_prompt()` gets `repository: Path` threaded in (already resolved
  and in scope at every call site — `resolve_route(...)` runs immediately before
  `_prompt` is called in all three adapters) and inserts the roadmap line, when
  present, right after the objective line. This is the substantive fix: it's the
  first time repository-level durable context reaches what the agent actually sees,
  for every provider, not just Claude Code.
- Also add `project_roadmap`'s path as a source in `build_instruction_bundle`'s
  `repository_instructions_and_workflow_policy` bucket (mirroring how the workflow
  README is already listed there) — for provenance consistency with its sibling
  sources, not because this bucket is consumed anywhere (it isn't, for any of its
  current entries either — see What we found). This plan does not attempt to fix
  that broader, already-known gap; it's noted, not solved, here.

### 3. Show a diff before confirming an update

`run_planning_conversation_interactive`'s confirm step
(`escape_ai_cli.py:1777`) prints current vs. proposed values (purpose/
current_stage/direction/durable_decisions) before asking "Update this repository's
saved roadmap with what was just discussed?" — reusing the same rendering the new
`roadmap show` command uses, not a second format.

### Multi-repository composition

Unchanged from the original version of this doc: `project_roadmap` is already
per-repository, so a multi-repository initiative composes for free — each
participating repository's own `.esc-ai/roadmap.yaml` is read/shown/prompt-injected
independently by whichever task actually executes in that repository. No
ecosystem-level artifact needed, and none is proposed.

## Non-goals

- No new artifact, schema, or file location — `project_roadmap`/`.esc-ai/roadmap.yaml`
  already exists and is the right shape; this plan extends its reach, not its shape.
- No `deprioritized`/`target_milestone` structured fields — `direction` (free text)
  already covers this ground; splitting it into more structured fields is a smaller,
  separable follow-up only worth doing if real use shows the free-text field isn't
  enough, not assumed necessary here.
- No deterministic "objective touches a deprioritized area" warning in `plan draft`
  — that depended on the `deprioritized` field above, which isn't being added. Left
  as a future idea if `direction`'s free text turns out to need that support.
- No fix to the broader "instruction bundle isn't read by any adapter prompt" gap
  beyond what this plan's design 2 already does for `project_roadmap` specifically
  — that's a pre-existing, larger gap affecting every other bucket in the bundle
  too, out of scope here.
- No change to `run_planning_conversation_interactive`'s AI-mediated flow itself
  beyond the diff fix in design 3 — the conversation mechanism, thresholds, and
  compaction logic from `ai-conversation-primitive.md` are unchanged.

## Open questions

1. Exact `roadmap set`'s non-interactive answers.json field-omission semantics
   (design 1: omitted field keeps current value) — needs confirming this doesn't
   surprise a caller expecting omitted-means-blank the way some other answer files
   in this system behave; leaning toward keep-current since a roadmap update is
   normally a small delta, not a full restatement, but worth a second look once
   built.
2. Whether `roadmap_prompt_line`'s content should be truncated/bounded for a very
   long `direction`/`durable_decisions` — today's `project_roadmap` has no length
   limit anywhere; unbounded free text flowing into every task's prompt could grow
   unboundedly over time. Not designed here; revisit if real use shows it's a
   problem.
