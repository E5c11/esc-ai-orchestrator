# JUnit-Report Verification Enhancement — Plan

**Status:** Implemented
**Date:** 2026-07-21
**Objective:** Where a verification gate check declares that it produces a JUnit XML
report, locate the file(s) that check's real invocation
([`provider-agnostic-gate-execution.md`](provider-agnostic-gate-execution.md))
actually produced and summarize them into a validated `verification-summary.json`,
attaching its path to the check's `report_path` field. This is task 4 of
[`task-orchestration-and-verification-loop.md`](task-orchestration-and-verification-loop.md)
("JUnit-XML enhancement"), scoped into its own doc because it turned out to have
real, previously-unrecorded design gaps once actually checked against the code.

## Why this is a separate plan

Same reasoning as `provider-agnostic-gate-execution.md`: the parent plan's one-line
description ("locate the JUnit XML that invocation produced and run it through
`summarize_junit`") undersells the actual work once checked against the real
codebase — see below. It's also cleanly separable: `execute_verification_plan`
already leaves a `report_path: null` extension point in every check result
specifically so this could land independently, without reopening that plan.

## What was checked, not assumed

- **`report-profile.schema.yaml` is completely unused.** A repository-wide grep for
  `report_profile`/`report-profile` outside the schema file itself, across
  `esc_exec/*.py` and `tests/*.py`, returns nothing. Nothing generates one, nothing
  references one from a component manifest's `paths`. `summarize_junit` requires a
  `profile_path` argument pointing at one, but today that file must be hand-authored
  from nothing — there's no equivalent of `generate_gradle_verification_profile` for
  it.
- **Gradle does not produce one combined JUnit XML file.** Its default test-results
  location is one file per test class:
  `<module>/build/test-results/test/TEST-<ClassName>.xml`. `summarize_junit`
  (`esc_exec/reporting.py:33`) takes a single `source: Path` whose root must be
  `testsuite`/`testsuites` — pointed at just one of those files, it would silently
  report only one class's results as if it were the module's entire test run. This is
  the same category of gap Phase 10 already found and fixed twice for Gradle
  (type-safe project accessors, KMP source-set layout) — a convention this system
  hadn't yet encountered, found by actually checking rather than assuming
  single-file JUnit output is universal.
- **Checks already flow from profile to plan unchanged, so no plan-schema change is
  needed.** `verification-plan.schema.json`'s `gates[].checks` is a bare
  `{"type": "array"}` with no item schema, and `build_verification_plan`
  (`esc_exec/task_context.py:180-185, 206-215`) copies each verification-profile
  check dict verbatim into the plan. Any new field added to a profile check (see
  Design) reaches `verification-plan.json`, and therefore
  `execute_verification_plan`, with zero changes to `build_verification_plan` itself.
- **Component real path, not manifest directory, is the right glob base.**
  `esc_exec/dependencies.py:145,162` establishes the pattern:
  `repository / manifest["component"]["path"]` is a component's real on-disk root.
  `manifest_path.parent` (where `generate_gradle_verification_profile` already writes
  the verification profile) is the `.esc-ai/components/<id>/` manifest-bundle
  directory per the consolidated layout, not the component's source directory — the
  cohesive-system plan is explicit that a generator "must never resolve one against
  the other's directory." The JUnit glob must be built from
  `manifest["component"]["path"]`, not `manifest_path.parent`.

## Design

1. **Schema:** add an optional `report` object to verification-profile checks
   (`schemas/verification-profile.schema.yaml`'s `$defs.checks` items):
   `report: {glob: <workspace-relative glob>, profile: <workspace-relative path to a
   report-profile.yaml>}`. Declared, not inferred, matching this system's existing
   preference for explicit facts over silent inference.
2. **Report-profile generation:** `generate_gradle_verification_profile` gains a
   companion default `esc-report-profile.yaml` (written next to the verification
   profile, same directory, if one doesn't already exist) —
   `{schema_version: 1, profile: {id: "<component>-report", format: junit-xml},
   limits: {max_failures: 10, max_message_chars: 500}}`, matching the existing
   example data's defaults. Only Gradle gets this automatically, matching how only
   Gradle has an automatic verification-profile generator today.
3. **Populate `report` on generated Gradle checks:** `focused`/`component` gate
   checks (both run the same module's tests) get
   `glob: "<component.path>/build/test-results/test/*.xml"`; the shared `final` gate
   check (`./gradlew test`, identical across every component and deduplicated by
   `build_verification_plan`) gets a repository-wide
   `glob: "**/build/test-results/test/*.xml"`. Both reference the new report-profile
   by its workspace-relative path.
4. **Multi-file aggregation in `esc_exec/reporting.py`:** extract the existing
   per-file XML-to-counters parsing out of `summarize_junit` into a shared helper,
   and add `summarize_junit_reports(sources: list[Path], profile_path: Path, output:
   Path, full_report_path: str | None = None) -> dict`, which aggregates totals and
   failures across every matched file into one combined `verification-summary.json`.
   `summarize_junit` itself becomes a thin one-file wrapper over the same helper —
   its external signature/behavior is unchanged, so the existing manual
   `esc-exec report summarize` CLI command and its tests keep working as-is.
   `full_report.path` for the multi-file case points at the first (sorted) matched
   file — a documented simplification, since the aggregated totals/failures (what
   automatic advancement actually needs) are correct regardless of which single file
   `full_report` happens to point at.
5. **Wire into `execute_verification_plan`:** after a check finishes with status
   `"passed"` or `"failed"` (a real invocation happened) and its plan entry carries
   `report`, resolve `report["glob"]` against `workspace_root`. Zero matches: leave
   `report_path: null`, no error — this is enrichment, not a requirement; a
   missing/misconfigured report must never turn an otherwise-clean gate red. One or
   more matches: call `summarize_junit_reports`, write to
   `run_dir/reports/<gate>-<check-id>-summary.json`, and set the check's
   `report_path` to that workspace-relative path. A malformed/unparseable XML file
   must not crash the run — caught the same way a subprocess launch failure already
   is, degrading to `report_path: null` rather than propagating.

## Non-goals

- Do not change `summarize_junit`'s existing external signature or single-file
  behavior — it stays a thin wrapper, still directly used by the manual `report`
  CLI command.
- Do not make report-profile authoring mandatory repository-wide — only components
  onboarded through the Gradle generator get one automatically for now.
- Do not fail a check or gate because its declared report glob matched nothing —
  see Design point 5.
- Do not build an npm/Jest equivalent report generator here — no evidence yet of
  npm's actual JUnit-reporter convention in this codebase's onboarded repositories;
  that's future work if/when real evidence calls for it, the same way npm build-system
  support itself followed Gradle only after being needed.

## Open questions

1. `full_report.path` pointing at only the first matched file when several exist is
   a real simplification, not a full multi-file browsing story — acceptable for now
   since aggregated totals/failures are what's actually consumed downstream (task 5's
   pass/fail determination), not the raw XML.
2. Whether non-Gradle build systems eventually need their own report-profile
   generator and glob convention is deferred until a real onboarded repository
   demonstrates the need, per this system's evidence-driven pattern.

## Task breakdown (ordered by dependency)

1. ~~Extend `schemas/verification-profile.schema.yaml`'s `$defs.checks`~~ — done
   2026-07-21: optional `report {glob, profile}` object added to `$defs.checks`.
   No runtime JSON-Schema validator reads this file (verification profiles are
   hand-checked by `build_verification_plan`, which already copies unknown check
   fields through untouched), so no other code needed to tolerate the new key.
2. ~~Refactor `esc_exec/reporting.py`~~ — done 2026-07-21:
   `summarize_junit_reports(sources: list[Path], profile_path, output,
   full_report_path=None)` aggregates totals/failures across every matched file;
   `summarize_junit` is now a one-line wrapper (`summarize_junit_reports([source],
   ...)`) with its external signature/behavior fully unchanged (all 3 pre-existing
   tests pass verbatim). New test:
   `test_summarize_junit_reports_aggregates_multiple_files` (2 files, combined
   totals/failures, single `verification-summary.json`). `tests/test_reporting.py`:
   3 -> 4 tests.
3. ~~`generate_gradle_verification_profile`: emit `report` + auto-generate default
   report-profile~~ — done 2026-07-21. Uses `manifest["component"]["path"]` (not
   `manifest_path.parent`, which is the `.esc-ai/components/<id>/` manifest-bundle
   directory) as the glob base, per the grounded-facts note above.
   `focused`/`component` checks get
   `{component.path}/build/test-results/test/*.xml`; the shared `final` check gets
   `**/build/test-results/test/*.xml`. A default `esc-report-profile.yaml`
   (`max_failures: 10, max_message_chars: 500`) is written next to the verification
   profile only if one doesn't already exist — a hand-authored one is left alone
   (`test_existing_report_profile_is_not_overwritten`). `manifest["paths"]
   ["report_profile"]` recorded alongside the existing `verification_profile` entry.
   `tests/test_task_context.py`: 6 -> 8 tests, all passing including every
   pre-existing one unchanged.
4. ~~Wire report lookup + summarize into `execute_verification_plan`~~ — done
   2026-07-21: new `_locate_report()` helper runs only for checks that actually
   executed (`status in {"passed", "failed"}`) and declare `report`; resolves
   `report["glob"]` via `workspace_root.glob(...)`, and on any match calls
   `summarize_junit_reports`, writing to
   `run_dir/reports/<gate>-<check-id>-summary.json` and setting `report_path` to
   that workspace-relative path. Zero matches or a malformed/unparseable XML file
   both degrade silently to `report_path: null` — caught explicitly
   (`except (OSError, ValueError): return None`), never propagated. A `report`
   declaration on a check that never actually ran (`skipped`/`not-run`) is
   correctly never looked up. `tests/test_verification_execution.py`: 6 -> 11 tests
   (single match, multi-file aggregation, no match, malformed XML, and
   skipped/not-run checks never get a report). Full execution-framework suite:
   383 -> 391 passing. Also manually verified end-to-end through the real
   `esc-exec verification execute` CLI against a scratch repository with a genuine
   failing JUnit test case inside an overall-passing gate command — confirming the
   design's core separation holds in practice: the check's `status`/`exit_code`
   stayed the authoritative `"passed"` (the command itself exited 0), while the
   JUnit enrichment separately surfaced the real per-test failure as additive
   detail, never overriding the gate's own result.

Depends on `provider-agnostic-gate-execution.md` (done). Unblocks task 5 in
`task-orchestration-and-verification-loop.md`.

## Status: complete (2026-07-21)
