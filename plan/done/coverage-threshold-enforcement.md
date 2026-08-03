# Coverage-Threshold Enforcement — Plan

**Status:** Implemented
**Date:** 2026-08-03
**Implemented:** 2026-08-03 — shipped and tested in esc-ai-execution-framework only
(no orchestrator changes). `reporting.py` gained `summarize_coverage_report` (one
parser for both Kover and JaCoCo, verified against real generated reports from both);
`contracts.py` gained a `coverage-summary` contract type; `verification_execution.py`'s
`_locate_report` now dispatches on the report profile's declared `format` and can
override a check's status from `passed` to `failed` when a coverage threshold isn't
met; `task_context.py`'s `generate_gradle_verification_profile` emits a coverage check
into the `component` gate (after the test check) when `testing.*.coverage` resolves,
via a small fixed `{"kover": "koverXmlReport", "jacoco": "jacocoTestReport"}` map.
Landing here doesn't mean every open question was resolved as originally framed — see
each one's own note.
**Objective:** The follow-on work `manifest-testing-facts-and-documentation-obligation.md`
explicitly scoped out of its own Non-goals: turn a component's declared
`testing.*.coverage` fact into a *real* verification-gate check — one that actually
generates a coverage report, parses it, and fails the gate when coverage falls below a
declared threshold — instead of `coverage.tool` being a fact an agent can only read,
never something escape-ai enforces.

## Why this is a separate plan

`manifest-testing-facts-and-documentation-obligation.md` deliberately stopped at
declaring facts ("Declaring the facts is this plan's whole scope; consuming them for
gate generation is a follow-on plan"). This plan is that follow-on, and stands alone
because it touches a different layer entirely: not manifests/indexing, but gate
*generation* (`generate_gradle_verification_profile`) and gate *execution*
(`execute_verification_plan`) — the same two functions `pre-flight-doctor-and-gate-
prerequisites.md` and `run-outcome-surfacing.md` already extended for unrelated
reasons, so this is one more addition to an established, well-tested seam, not a new
subsystem.

## What we found

**Kover and JaCoCo emit the same XML schema — verified against real, already-generated
reports in both grounding repositories, not assumed.** `ampm-kmp`'s
`feature/home/build/reports/kover/report.xml` (Kover 0.9.6) and `ampm-backend`'s
`auth/build/reports/jacoco/test/jacocoTestReport.xml` (JaCoCo) both have the identical
structure: a `<report>` root containing one `<package>` per package (each with nested
`<class>`/`<sourcefile>` detail), followed by direct-child, report-level
`<counter type="INSTRUCTION|BRANCH|LINE|METHOD|CLASS|COMPLEXITY" missed="N"
covered="M"/>` elements — the whole-module totals. Kover's XML report is, in practice,
JaCoCo-format-compatible. This means **one parser handles both tools** — no
tool-specific branching needed, despite `ampm-kmp` and `ampm-backend` using entirely
different coverage tools. Confirmed by direct inspection, not documentation:
`tail -c 2000` of each real file shows the exact same closing
`<counter type="LINE" missed="X" covered="Y"/>`-shaped tail before `</report>`.

**No npm equivalent to generalize against.** `generate_gradle_verification_profile`'s
own docstring already says "automatic verification-profile generation currently
supports Gradle only" — there is no `generate_npm_verification_profile` at all to keep
in parity with (npm components need a hand-authored `esc-verification-profile.yaml`
today, regardless of this plan). Scoping this plan's gate-generation half to Gradle
isn't a new narrowing carved out of laziness — it matches the generator's existing,
already-Gradle-only scope. (Also: JS coverage tools/report formats vary too widely —
Istanbul/nyc, Vitest's coverage output, etc. — to safely generalize without a concrete
example the way Kover/JaCoCo's shared schema allows; inventing that support with no
real repository to ground it against would violate this codebase's own "don't build
what nothing needs yet" discipline.)

**`reporting.py`'s `summarize_junit_reports` is the direct precedent to mirror**, not a
new pattern: a report-profile YAML (`esc-report-profile.yaml`) declares
`profile.format` plus tool-specific `limits`; `verification_execution.py`'s
`_locate_report` glob-matches real report files and calls the matching summarizer,
attaching `report_path` to the check result. Today `_locate_report` is hardcoded to
`summarize_junit_reports` regardless of the report profile's own declared format —
confirmed by reading it directly, no format dispatch exists yet.

## Design

### 1. `summarize_coverage_report` — one parser for both tools

New function in `reporting.py`, same shape/precedent as `summarize_junit_reports`:

```python
def summarize_coverage_report(source: Path, profile_path: Path, output: Path, full_report_path: str | None = None) -> dict[str, Any]
```

Parses the `<report>` root's direct-child `<counter>` elements only (never descending
into `<package>`/`<class>`, which hold the same-named counters at finer grain — the
report-level ones are what "overall coverage" means here). The report profile declares
which counter `type` represents "coverage" and what threshold applies:

```yaml
# esc-report-profile.yaml (coverage variant)
schema_version: 1
profile: {id: content-coverage-report, format: coverage-xml}
limits: {counter_type: LINE, threshold: 80}
```

`counter_type` defaults to `LINE` (the most commonly cited "coverage %" in practice) if
omitted — matches both real repos' own habit of citing line coverage. Output document
(new `coverage-summary` contract, added to `contracts.py` alongside
`verification-summary`):

```json
{
  "schema_version": 1,
  "coverage": {"profile": "content-coverage-report", "source_format": "coverage-xml", "status": "passed", "generated_at": "..."},
  "totals": {"counter_type": "LINE", "missed": 87, "covered": 1024, "percent": 92.17},
  "threshold": {"required": 80, "met": true},
  "full_report": {"path": "...", "media_type": "application/xml"}
}
```

`status` is `"passed"` if no threshold declared (report-only, informational) or
`percent >= threshold`; `"failed"` otherwise. Malformed/missing counter data is a real
error (`ValueError`), not a silent zero — matches `summarize_junit_reports`'s own
"never invent numbers" discipline (see `run-metrics.json`'s "never estimate missing
token usage" precedent elsewhere in this codebase).

### 2. `execute_verification_plan` lets a report's own status override a passing exit code

The real reason this needs a code change, not just a new summarizer: a coverage
report-generation command (`./gradlew :content:koverXmlReport`) exits 0 regardless of
the coverage percentage — the *tool* isn't enforcing anything, escape-ai has to. Today
`_locate_report`'s result only ever attaches `report_path`; it never feeds back into
the check's own `status`. This plan changes that specifically for a `coverage-xml`
summary: after locating and summarizing the report, if the summary's own
`coverage.status` is `"failed"` (threshold not met), the check's status flips from
`"passed"` to `"failed"` even though `exit_code == 0` — same `stopped = True`/
`worst_status` consequences as any other failed check, no new control flow invented.

### 3. `generate_gradle_verification_profile` emits a coverage check when declared

When the component's resolved `testing.common.coverage` (component-level `testing`
overrides checked the same way `manifests.merged_testing` already resolves them —
reused directly, not reimplemented) declares a `tool`, add one more check to the
`component` gate (alongside the existing `<component>-tests` check, not replacing it):

```yaml
component:
  - {id: content-tests, command: [./gradlew, :content:test], report: {...junit...}}
  - id: content-coverage
    command: [./gradlew, "<gradle-task-for-tool>"]
    report: {glob: "<component_path>/build/reports/<tool>/**/*.xml", profile: esc-coverage-report-profile.yaml}
```

`<gradle-task-for-tool>` is resolved from a small fixed map (`{"kover": "koverXmlReport",
"jacoco": "jacocoTestReport"}`) keyed by the declared `coverage.tool` string — an
explicit, small lookup, not an attempt to guess arbitrary tool task names. An unknown
tool name is a real error at profile-generation time (fail loud, don't silently skip
coverage enforcement the manifest asked for). A second file,
`esc-coverage-report-profile.yaml`, carries the coverage-shaped profile alongside the
existing `esc-report-profile.yaml` (JUnit) — see open question 1, resolved.

## Non-goals

- npm/JS coverage support. No generator exists to extend (see "What we found"); revisit
  only once a real npm-based repository with a concrete coverage tool needs it.
- Per-platform coverage thresholds (a KMP component with different Android vs. iOS
  coverage expectations). Both grounding repositories declare coverage at the `common`
  level only; this plan resolves coverage from `testing.common` alone, matching that
  reality, not from `testing.platforms.<name>.coverage`. Extend later if a real
  repository needs platform-specific coverage enforcement.
- Enforcing thresholds retroactively against already-onboarded repositories that never
  declared `coverage.threshold`. No threshold declared means report-only (`status:
  "passed"` unconditionally) — this plan never turns on enforcement a repository didn't
  opt into.
- Trend tracking, historical comparison, or per-file/per-package threshold granularity.
  Whole-module percentage against one declared threshold is the entire scope.

## Open questions

1. **Resolved 2026-08-03, exactly as leaned:** a second file,
   `esc-coverage-report-profile.yaml`, generated alongside the existing
   `esc-report-profile.yaml` — kept separate since the two profiles' `limits` shapes
   are unrelated (`max_failures`/`max_message_chars` vs. `counter_type`/`threshold`).
2. **Resolved 2026-08-03, exactly as leaned:** `reporting.py`'s `_coverage_profile`
   validates `limits.counter_type` against the fixed `COVERAGE_COUNTER_TYPES` set
   (`INSTRUCTION`/`BRANCH`/`LINE`/`METHOD`/`CLASS`/`COMPLEXITY`), raising `ValueError`
   for anything else. Same enum also added to `contracts.py`'s `ENUMS["coverage-summary"]`.
3. **Resolved 2026-08-03, exactly as leaned:** `generate_gradle_verification_profile`
   appends the coverage check after the existing test check in the `component` gate's
   list; no changes were needed to `execute_verification_plan`'s iteration order, since
   `stop_on_failure` already means a failed test check (earlier in the list) prevents
   the coverage check from running at all — checked directly against the real
   implementation, not just assumed.
