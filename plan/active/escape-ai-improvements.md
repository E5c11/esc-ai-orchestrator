# escape-ai — issues found while dogfooding (ampm-kmp/ampm-backend/ampm-contracts)

**Status:** Distilled 2026-08-03 — every finding below has been turned into a proper
design doc; this log is kept as the original source material, not as open work
itself. Where each finding landed:

- **#1** (task-ID discoverability) and **#2** (`plan draft` questions not
  inspectable) → [`cli-discoverability.md`](../done/cli-discoverability.md)
  (**implemented** 2026-08-03)
- **#3** (no pre-flight environment check), **#4** (worktrees don't inherit
  gitignored config), **#5** (gates don't declare prerequisites), **#6** (failure
  categorization left to the human), and the scoped-verification half of **#8**
  (eager `@Bean` pulling in unrelated modules) →
  [`pre-flight-doctor-and-gate-prerequisites.md`](../done/pre-flight-doctor-and-gate-prerequisites.md)
  (**implemented** 2026-08-03)
- **#7** (dependency chain not printed) and **#9** (a no-op "succeeded" run is
  indistinguishable from a real one — also found to silently auto-advance dependent
  tasks, worse than originally described) →
  [`run-outcome-surfacing.md`](../done/run-outcome-surfacing.md) (**implemented**
  2026-08-03)
- **#8**'s underlying `ampm-backend` bug (eager, non-conditional credential-backed
  bean crashing full-context tests) is a real issue in that repository, not
  escape-ai's — flagged back to it separately, not part of any escape-ai plan.
- **#10** (a fix task's passing tests don't mean the fix works; Crashlytics-only
  logging isn't locally visible) — no new plan doc. The concrete, escape-ai-side ask
  (a task whose completion condition is "add logging for this failure path" should
  default to asking for *locally visible* logging, not just a crash-reporting SDK
  call) is small enough to fold directly into the instruction-bundle wording next
  time that template is touched, rather than warranting its own design doc. The
  broader lesson (a fix isn't verified until exercised against the real running
  system) is a human-process discipline point, not something escape-ai's tooling
  itself can enforce.

Running log of real friction points hit while using `escape-ai` (headless/BACKDOOR mode) to
orchestrate the `user-identity-spring` and `referral-premium-spring` initiatives across
`ampm-kmp`, `ampm-backend`, and `ampm-contracts`. Written for whoever preps this for public
release — each entry is a real thing that happened, not a hypothetical.

---

## 1. Multi-repo task IDs aren't discoverable, and the error on a wrong guess doesn't help

`BACKDOOR.md` documents that a multi-repository plan's task IDs are `<initiative-id>-<repository-id>`,
but nothing in the CLI itself surfaces this. I initially ran:

```
escape-ai task run ampm-backend user-identity-spring --yes
```

and got:

```
INVALID    no task.yaml found for `user-identity-spring` in `ampm-backend`
```

There's no "did you mean `user-identity-spring-ampm-backend`?" — the tool clearly knows the
real task ID exists (it's sitting right there in `.esc-ai/workflows/active/`), so the error
could just say so. A public user without the naming convention memorized would have to go
spelunking in the filesystem to recover from this.

**Suggested fix:** on an unknown task ID, list the task IDs that *do* exist for that repository
(or fuzzy-match against the initiative-id prefix) instead of a flat "not found."

---

## 2. `plan draft`'s pending questions aren't inspectable via the CLI

`repository analyze --json` prints the actual `semantic_questions` array so you know exactly
what's being asked. `plan draft` only prints a count:

```
6 question(s) require your input before this can be applied.
```

There's no `plan draft ... --json` or `plan status --json` that dumps the actual question
prompts. The only reason I knew the answer shape (`components`/`scope_boundary`/
`completion_conditions`/`rollout_needs`) was because `BACKDOOR.md` documents it verbatim — a
user relying on `--help` output alone (`plan draft -h`, `plan answer -h`) would have no way to
discover the expected JSON schema for `plan-answers.json` without external docs.

**Suggested fix:** mirror `repository analyze --json`'s pattern — have `plan draft` (or a new
`plan questions <initiative-id> --json`) print the actual pending questions/schema, not just a
count.

---

## 3. No pre-flight environment check before burning a real dispatch attempt

Four consecutive `task run ... --yes` attempts against `ampm-backend/user-identity-spring-ampm-backend`
failed, each for a different **environment** reason, not a code defect:

1. Attempt 1: `:core:web:compileKotlin` → 401 from `maven.pkg.github.com` — no `GITHUB_TOKEN`/`PAT`
   configured anywhere on the machine for headless Gradle.
2. Attempt 2 (after fixing #1): `final.repository-tests` → `ConnectException` — no Docker/container
   runtime installed at all (no Docker Desktop, no Colima, no Podman), so the Postgres-backed
   Spring context tests can't start.
3. Attempt 3 (after installing Colima + starting `docker compose up -d`): same `ConnectException`
   — turned out `compose.yaml` maps `5432:5432` but `application.yaml` hardcodes
   `jdbc:postgresql://localhost:15433/ampm`. A real, pre-existing port mismatch in the repo itself,
   unrelated to escape-ai, but escape-ai had no way to catch it before spending a full agent
   dispatch on it.
4. Attempt 4 (after starting Postgres on the port the app actually expects): passed.

Every one of these could have been caught by a cheap, local, no-agent-dispatch pre-flight check
run against the *task's declared verification gates* before the real (costly, subscription-metered)
agent run starts — e.g. "can these gate commands even resolve their dependencies / reach the
services they need?" A dry-run mode that just attempts dependency resolution and service
connectivity for the verification plan's gates, without doing any actual work, would have
surfaced all three gaps in under a minute instead of four full attempt cycles.

**Suggested fix:** a `escape-ai task doctor <repo> <task-id>` (or automatic pre-flight before
`task run`) that resolves the verification plan's declared gates' dependencies/connectivity
and reports gaps, before dispatching a real agent.

---

## 4. Disposable worktrees don't inherit gitignored local config

Each task runs in its own `git worktree` under `.esc-ai/worktrees/<task-id>/` on its own branch.
Gitignored files that live in the main checkout (`local.properties`, containing the `PAT` key
used for GitHub Packages auth) are **not** present in the fresh worktree — which is exactly
correct git-worktree behavior, but it means any task whose build depends on a gitignored local
credential file will fail the same way attempt 1 above did, on literally every repo that follows
this (extremely common) local-secrets pattern, unless the operator knows to manually copy the
file into the worktree first.

**Suggested fix:** let a repository's onboarding manifest (`esc-execution.yaml` or similar)
declare a list of gitignored files to copy from the main checkout into every fresh worktree
before a task runs (e.g. `local.properties`, `.env`). Should be opt-in and clearly documented
since it's copying secrets around, but the alternative today is silent, confusing build failures.

---

## 5. Verification gates don't declare their own prerequisites

The `final.repository-tests` gate (`./gradlew test` at the repo root) transitively needs a live
Postgres — nothing in the verification plan (`verification-plan.json`) or the task's `task.yaml`
says so up front. Combined with #3 above, this means the *first* signal that a service dependency
is missing is a failed run deep in a `Caused by` chain in a raw Gradle log, not a declared
precondition anyone could check ahead of time.

**Suggested fix:** let verification gates optionally declare external prerequisites (e.g.
`requires: [docker, postgres:5432]`) so a pre-flight check (see #3) has something concrete to
check, and so the failure report itself can say "this gate needs Postgres reachable, which
wasn't" instead of a bare Gradle stack trace.

---

## 6. Failure categorization is left entirely to the human reading raw logs

`BACKDOOR.md` itself only distinguishes two blocker shapes: "normal task failure" vs.
"architecture-coverage hard stop." In practice I hit a third and fourth shape it doesn't
mention at all — dependency-resolution auth failures and service-connectivity failures — and
for all of these the tool's own output (`checkpoint.yaml`, `verification-result.json`) just
says `status: failed` / `component.auth-tests (failed, exit_code=1)` with no hint of *why*
beyond "go read the stdout/stderr log yourself." Every one of the four attempts above required
manually grepping `Caused by` chains out of raw Gradle logs to figure out which of several
possible causes was responsible.

**Suggested fix:** a lightweight failure classifier over the verification logs (dependency
resolution / connection refused / assertion failure / compile error / other) surfaced directly
in the checkpoint summary, so the human (or the next agent attempt) doesn't have to re-derive it
from scratch every time.

---

## 7. `plan apply`'s straight-chain dependency ordering (already a known limitation, confirmed still current)

`BACKDOOR.md` already documents this, but confirming it's still exactly true as of this session:
a multi-repo `plan apply` always chains tasks in the declared `repositories` list order (each
`depends_on` only the one immediately before it), never genuine branching, regardless of whether
the work actually requires that order. For both initiatives here (`ampm-backend` →
`ampm-contracts` → `ampm-kmp`), that ordering happens to be correct, but nothing about the plan
draft step surfaces "here's the dependency chain I'm about to lock in, confirm or edit it" — you
only find out by reading `task.yaml`'s `depends_on` field yourself or asking `plan ready`.

**Suggested fix:** since this is already flagged as known, just noting it's not fixed yet and
still surprising on first contact — `plan draft`/`plan apply` output could print the resulting
dependency chain explicitly (not just repositories-and-components) so it's visible without
reading YAML.

---

## 8. A single eagerly-created `@Bean` with no default can crash the *entire* app context for unrelated work

The full-context gate's fifth attempt failed for a third, distinct environment reason:
`AmpmBackendApplicationTests.contextLoads()` wires up `:migration`'s `FirebaseAdminConfig.firebaseApp()`
bean, which does a bare `FileInputStream(credentialsPath)` against `MIGRATION_FIREBASE_CREDENTIALS_PATH`
(a Firebase Admin service-account JSON) with **no default and no conditional/lazy wiring** — so a
missing credential for one unrelated legacy-migration feature crashes Spring context startup for
*every* test that needs the full application context, including tests for completely unrelated
modules (`auth`, `portal`). `ampm-backend`'s own deployment doc already flags this exact footgun
happened once before in prod (`wiki/architecture/deployment.md`: "a missing credential crashes the
entire app at startup, not just the legacy-migration login path"), so it's a known, previously-bitten
issue in the *product*, not just friction on my end — but it's also the third distinct
environment/credential gap this one task's verification gate exposed (after #GitHub-PAT and
#Docker/Postgres), each requiring a full attempt cycle to discover.

This is the strongest evidence yet for #3/#5 above: a task scoped to 3 components (`auth`,
`core-api`, `core-web`) had its verification blocked five times by things entirely outside that
scope, each only discoverable by running the real thing and reading a raw stack trace.

**Suggested fix (beyond #3/#5):** the underlying app-side bug (eager, non-conditional credential-backed
beans reachable from a "full context" test with no way to exclude them) is worth flagging back to
the repo itself as a resiliency issue independent of escape-ai. From escape-ai's side: a way for a
task to say "only verify the components/modules actually in scope, not the whole application
context" would have avoided pulling in `:migration` at all for an `auth`/`core-api`/`core-web`-scoped
task.

---

## 9. A "succeeded" run can mean "wrote zero code and asked a clarifying question instead"

`referral-premium-spring-ampm-backend`'s first real attempt (attempt 2, after the index-staleness
retry) exited with `status: succeeded` and `exit code 0` — indistinguishable at the CLI/notification
level from a real completed implementation (same as `user-identity-spring-ampm-backend`'s actual
successful run). But `run.json` shows `"worktree": {"kept": false}` — no code was written at all.
The run's only artifact was a research summary ending in a literal question back to whoever
dispatched it: *"I'd treat the PremiumController ... as the safe, self-contained slice to implement
here first — want me to proceed with that, or are you coordinating the `:referral` module and
contracts work in parallel elsewhere?"*

The underlying model made a reasonable, well-evidenced judgment call (the task's completion
conditions span 3 repos and a not-yet-created Gradle module, while this task's own component scope
was restricted to `:user`) — but a fire-and-forget `--yes` autonomous dispatch has nobody to answer
that question mid-run, so the "safe" choice of asking instead of proceeding just burns a full attempt
for nothing. Nothing in the CLI output, `resume --json`, or the notification summary distinguishes
this from a real completed task — I only found out by reading `run.json`'s worktree-not-kept field
and the artifact content directly.

**Suggested fix:** surface a distinct status (e.g. `succeeded-no-changes` or
`succeeded-needs-clarification`) whenever a run completes with an empty/discarded worktree diff, so
this is visibly different from a real completed implementation without having to read `run.json` by
hand. Separately, since `--yes`/full-autonomy mode has no one to answer mid-run questions, the
instruction bundle handed to the agent could say so explicitly ("no human will see clarifying
questions until this run ends — make the reasonable autonomous call and document it in the summary
instead of asking").

---

## 10. A dispatched "fix" task's own passing tests didn't mean the fix actually worked — only real on-device testing caught this

`identity-sync-fix` correctly diagnosed and fixed a real bug (`SpringApiClient.patch()`'s stale
manual bypass, see #9) with proper unit test coverage, all green. But live-testing the *actual
running app* against the real deployed backend afterward showed the original symptom (identity
edits silently failing to sync) **still reproduced identically** — the escape-ai task's fix was
necessary but not sufficient. A **second, independent bug** was hiding behind the first the whole
time: the backend's `@Email` validation rejects an empty-string email (`400`) even though it
accepts a missing one, and `SpringUserIdentityApi.saveIdentity()` was sending anonymous users'
locally-empty-string email straight through as `""` rather than `null`. Nobody could see this
until the *first* bug (the transport-layer one) was fixed enough for a real request to actually
reach the server and get a real validation response back — before that, every attempt failed at
the transport layer for a different reason, masking the validation bug entirely.

Root-causing this second bug required stepping outside the escape-ai loop entirely: Firebase
Crashlytics' `recordException()`/`log()` (which the first fix added for visibility) are
fire-and-forget uploads with **zero local logcat output by design** — the "add logging" part of
the first fix's own completion condition didn't actually make the failure visible on-device the
way plain `println`/`Log.e` would have. I had to add temporary `println`-based diagnostic logging
myself, rebuild, reproduce the failure, read the real exception out of logcat, then remove the
diagnostic and apply the real fix directly — a manual step outside any escape-ai task.

Also worth flagging: an intermediate rebuild+install cycle silently used a stale APK despite a
successful merge (`24 tasks executed / 631 up-to-date` where a full change should have triggered
far more recompilation) — only caught by comparing against a `--rerun-tasks` forced rebuild
(`655/655 executed`) and noticing the local-vs-server state still disagreed. Trusting "the install
task said BUILD SUCCESSFUL" without an independent state check (server value vs. local value) would
have hidden this.

**Suggested fix:** a task's own "add logging for this failure path" completion condition should
specify *locally visible* logging (plain stdout/Log, not just a crash-reporting SDK call) whenever
the goal is developer-facing debuggability, not just production telemetry — the two have very
different audiences and Crashlytics-only logging serves neither during active development. More
broadly: **a fix task passing its own tests should never be treated as "verified" until it's been
exercised against the real running system it claims to fix** — this is the single most important
finding from this whole dogfooding session, and it took a working end-to-end on-device retest
(with server-side confirmation, not just "the app didn't crash") to catch a fix that looked
complete by every automated signal available.

## Net takeaway

None of the actual *product* work was blocked by escape-ai's core orchestration model — the
plan/answer/apply/run flow itself worked exactly as documented. Every issue above was about
**surfacing environment/config problems before spending a real dispatch on them**, and about
**discoverability of the tool's own expected inputs/outputs** without already having read
`BACKDOOR.md` end to end. For a public release, front-loading a pre-flight/doctor pass and
making `plan draft`'s questions and `task run`'s failures machine-inspectable would remove most
of the friction documented here.
