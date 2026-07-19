# Plan docs

Standalone design docs for escape-ai, one concern per doc (see each doc's own "Why
this is a separate plan" section for the reasoning behind that split). Organized by
where a doc's work actually stands, not by topic or date:

- **`active/`** — currently the focus: either genuinely in progress, or fully
  designed and next in line to build. `**Status:** Active` doesn't require that code
  has already been written — it means this is where attention is currently going,
  as opposed to `future/`'s "designed but not prioritized."
- **`future/`** — proposed, designed to varying depths, not currently prioritized.
  Someday/maybe, not abandoned.
- **`done/`** — substantially implemented and shipped in the codebase. Kept as
  documentation of what was built and why, not as work still to do. A doc landing
  here doesn't mean every open question in it was resolved — see its own "Open
  questions" section for anything still loose.

A doc moves between folders as its status changes — update its own `**Status:**`
field to match (`Proposed` / `Active` / `Implemented`) when you move it, so the two
never drift apart the way they had before this reorganization (2026-07-20): every
doc said `Proposed` regardless of its real state.

No fourth folder for abandoned/superseded ideas yet, since nothing currently needs
one — add one (e.g. `superseded/`) if that actually happens, rather than
pre-creating it empty.
