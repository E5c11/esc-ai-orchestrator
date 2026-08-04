# Project Vision and Direction — Plan

**Status:** Proposed
**Date:** 2026-08-04
**Objective:** Give a repository — and, by composition, a multi-repository
initiative — a persistent, human-authored statement of current direction and
priorities that escape-ai surfaces during planning and execution. Closes the one
scenario from the CLI-completeness review with no home anywhere in the current
design.

## Why this is a separate plan

None of the three existing "plan new work"/"Configure system" mechanisms cover
this. `plan draft`'s objective is per-initiative and stateless across initiatives;
the workflow-policy schema (`schemas/workflow-policy.schema.yaml`,
esc-ai-execution-framework) only has a pointer-shaped mention — "where active
business roadmaps live," as free prose inside `.esc-ai/workflows/README.md`, per
`cohesive-system-integration-and-onboarding.md` — with no structured field and
nothing that reads it back into planning. This is genuinely new capability, not a
wiring gap like the other two docs in this batch
(`multi-repository-dependency-graph-planning.md`,
`configure-system-policy-profiles.md`).

## What we found (grounding)

- The instruction precedence order
  (`cohesive-system-integration-and-onboarding.md`, "Instruction precedence")
  already has a natural slot: level 4, "Repository-specific instructions and
  workflow policy." Vision is repository-specific, human-authored, and
  lower-precedence than architecture/execution-framework core content — it fits
  this existing level without inventing a seventh.
- `.esc-ai/workflows/README.md` already follows a "structured frontmatter for
  enumerable fields, free-form prose for the rest" convention
  (`schemas/workflow-policy.schema.yaml` + prose skeleton) — the same shape fits a
  vision document directly.
- Ecosystems (named repository groupings in `system.yaml`) have no file of their
  own to hold ecosystem-level content — any "vision" concept pitched above a single
  repository has nowhere to live without inventing a new artifact location.

## Design

- New file, `.esc-ai/vision.md`, one per onboarded repository, sibling to
  `.esc-ai/workflows/README.md`. Structured frontmatter for the fields worth
  machine-reading, free prose for the rest:
  - `priorities`: ordered list of short strings (current focus areas, highest
    first).
  - `deprioritized`: list of short strings (explicitly out of scope / paused
    areas) — the field that actually earns its keep: it lets planning flag "this
    objective touches something explicitly deprioritized" with a plain
    keyword/component-name match against a short authored list, no AI judgment
    call required.
  - `target_milestone` (optional): free string, no enum — a milestone name/date
    meaningful to the user, not a system-tracked deadline.
  - Free-form prose section below the frontmatter: the actual narrative direction,
    same shape as workflow policy's prose section.
- New commands: `escape-ai vision set <repository-id>` (opens `$EDITOR` on the
  file, creating a minimal template if absent — a vision statement resists being
  decomposed into typed questions the way onboarding/planning answers do, so this
  is deliberately not a guided Q&A) and `escape-ai vision show <repository-id>`
  (prints the file, or "no vision set yet for `<id>`" if absent — never an error).
- Interactive menu: a new action inside "Configure system"
  (`run_configure_interactive`, `escape_ai_cli.py:1978`) — "Show / set project
  vision" — reusing the same submenu pattern as the existing provider/repository-
  list actions, rather than a seventh top-level menu item; vision is set rarely and
  read often, the same shape as everything else already living under Configure
  system.
- Wiring into planning: `draft_plan` (`escape_ai_cli.py:929`) reads each involved
  repository's `.esc-ai/vision.md` if present and:
  - Surfaces `deprioritized` matches against the stated objective/routed
    components as a printed warning in `render_plan_draft` (deterministic
    substring/keyword match against the authored list, not an AI judgment call —
    consistent with this system's preference for deterministic checks over
    speculative AI classification).
  - Passes the full vision content into the instruction bundle at precedence
    level 4, alongside workflow policy, so any agent executing a task from this
    initiative sees it — no new bundle-assembly mechanism, `order_instruction_
    bundle` already composes this precedence level.
- Multi-repository initiatives compose for free: since vision is per-repository, a
  multi-repo `plan draft` just reads and surfaces every involved repository's own
  vision — no separate ecosystem-level artifact needed, which resolves the
  "ecosystems have no file of their own" problem by not requiring one.

## Non-goals

- No AI-authored or AI-suggested vision content — this is a human statement of
  intent, authored by nobody but the user, same as workflow policy's prose section
  today.
- No automatic conflict *blocking* — a deprioritized-area match is a printed
  warning during `plan draft`, not a hard stop; the human decides whether the
  objective is a deliberate exception (matches this system's existing discipline of
  surfacing conflicts rather than silently choosing, per Instruction precedence's
  own rule — here the human is the tiebreaker, not the orchestrator).
- No ecosystem-level vision artifact/file — deferred; per-repository composition
  covers the multi-repo case without it (see Design).
- No vision versioning/history UI beyond whatever git already gives a committed
  file — no new persistence mechanism.

## Open questions

1. Exact frontmatter schema (field names/types above are a first proposal, not
   validated against a schema file yet) — should probably get its own
   `schemas/vision.schema.yaml` in esc-ai-execution-framework mirroring
   `workflow-policy.schema.yaml`'s structure, but not drafted here.
2. Whether `deprioritized` matching should extend beyond a plain substring/keyword
   check against the raw objective (e.g. also matching routed component IDs) —
   likely yes, but the exact rule needs designing against real onboarded-repository
   data, not assumed.
3. Whether `vision show`/`set` needs a non-interactive `--json` mode (matching
   `repository analyze --json`'s precedent from `cli-discoverability.md`) for
   scripted/BACKDOOR-mode use — probably yes eventually, not designed here.
