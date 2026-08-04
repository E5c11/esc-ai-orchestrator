# Configure System — Policy Profile Authoring and Selection — Plan

**Status:** Proposed
**Date:** 2026-08-04
**Objective:** Let "Configure system" author or select a named `policy.yaml` profile
as the default applied when planning/executing a task, closing the gap
`cohesive-system-integration-and-onboarding.md` named but
`interactive-menu-completeness.md` deliberately left out of scope.

## Why this is a separate plan

`plan/active/cohesive-system-integration-and-onboarding.md`'s "Unified CLI and
interface" section names "permission policies — author or select a named
`policy.yaml` profile" as part of Configure system's job.
`plan/done/interactive-menu-completeness.md` (Implemented 2026-08-03) explicitly
scoped this out under Non-goals ("A general system settings/config editor... is a
separate decision"). This is that separate decision, made concrete. Distinct from
`plan/done/pre-flight-consent-and-bounded-autonomy.md`, which governs per-task
runtime consent/enforcement (layers 1-4, already implemented and unchanged by this
doc) — this plan is about which policy a task *starts from* by default, not about
re-litigating that enforcement model.

## What we found

- `default_policy()` (`esc_orchestrator/escape_ai_cli.py:587`) is a single hardcoded
  function returning one fixed read+edit+execute+network grant — its own comment
  block (`escape_ai_cli.py:504-517`) documents it as a placeholder "pending real
  Configure system support."
- The policy schema already exists and is stable
  (`schemas/policy.schema.yaml`, esc-ai-execution-framework): `id`, `description`,
  `permissions` (per-category allow/ask/deny), `limits` (`max_parallel_agents`,
  `max_run_seconds`), `approvals`. `examples/contracts/policy.yaml`
  (`readonly-review`) shows the shape concretely, but it's an example, not a
  selectable option anywhere.
- `run_configure_interactive` (`escape_ai_cli.py:1978`) has exactly three real
  actions today (show provider, connect/switch provider, list repositories) plus
  Back — no policy action at all.
- Nothing today lets a user see, choose, or author more than the one hardcoded
  profile; `task run`'s own preview output already says so explicitly (README:
  "Policy is still a genuine placeholder").

## Design

- A small set of named, built-in policy profiles shipped with escape-ai, not
  user-authored YAML from scratch on first use — mirrors the existing
  `readonly-review` example plus the category-level grant `default_policy()`
  already returns today, promoted from hardcoded function to a named, selectable
  profile (e.g. `standard-autonomous`). Minimum starting set: those two.
- The machine-local catalog (`system.yaml`) gains a `default_policy: <profile-id>`
  field, resolved by the registry the same way `active_provider` already resolves a
  connected provider — no new resolution mechanism, reuse the existing
  registry read/write pattern.
- "Configure system" submenu gains a "Show / select default policy" action: lists
  built-in profiles with their `description` field, lets the user pick one, writes
  the choice to the registry.
- `default_policy()` becomes `resolve_default_policy(registry)`, reading the
  registry's `default_policy` field and falling back to today's hardcoded grant if
  unset — an existing installation with no configured choice behaves exactly as it
  does today, no silent behavior change on upgrade.
- A task overriding its policy explicitly (per the cohesive plan's own line) is
  real, named, future scope — not assumed built here (see Open questions).

## Non-goals

- No free-form policy authoring UI (a form building an arbitrary
  `permissions`/`limits`/`approvals` document field-by-field) — that needs real
  design work (per-field validation, preventing an accidentally-too-wide grant) and
  isn't needed to close today's actual gap, which is "there is only ever one
  hardcoded choice, full stop." Hand-editing `policy.yaml` directly remains
  available for a genuinely custom profile, same as `system.yaml` itself today.
- No per-task policy override UI in this doc — see Open questions.
- No change to the enforcement mechanism itself (`HARD_DENY_SETTINGS`, worktree
  isolation, consent recording) — all already correct per
  `pre-flight-consent-and-bounded-autonomy.md`; this doc only changes which named
  profile a task starts from.

## Open questions

1. Exact built-in profile set beyond the two identified (today's default +
   `readonly-review`) — e.g. a no-network variant for a task that shouldn't reach
   external services. Left for implementation-time judgment, not decided here.
2. Whether a task can override the registry default at `plan apply`/`task run` time
   (e.g. `task run --policy readonly-review`) — a real, named need per the cohesive
   plan, but a distinct, smaller follow-up once the registry-level default exists;
   not blocking this doc's core deliverable.
3. Whether profiles are purely a fixed, shipped set or the registry can register
   additional named profiles pointing at custom `policy.yaml` files — leaning toward
   allowing the latter (register-a-path, same pattern as `route add`), but not
   designed to schema detail here.
