# Active Orchestrator Workflows

The current product-level direction is captured in
[`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md).
Create capability-specific active workflows here as that plan is implemented; do not
duplicate the master plan into each workflow.

## Sequence

| # | Capability | Outcome | Status |
|---|---|---|---|
| 1 | [Bootstrap control plane](../archive/bootstrap-control-plane.md) | HTTP submission/observation, SQLite persistence, scheduling, replaceable runtime boundary | Complete |
| 2 | [Framework composition protocol](../archive/phase-1-framework-composition.md) | Repo-local `.esc-ai/` directory, per-task run-output resolution, task context resolves architecture documents without hard-coded checkout paths | Complete |
| 3 | [Onboarding analysis engine](../archive/phase-3-onboarding-analysis-engine.md) | Read-only repository proposal endpoints backed by the execution framework's analysis engine | Complete |
| 4 | [Manifest construction](../archive/phase-4-manifest-construction.md) | Answers apply through the same `Store`-backed flow, non-destructively | Complete |
| 5 | [Workflow inheritance bootstrap](../archive/phase-5-workflow-inheritance-bootstrap.md) | Onboarding's final step wired through the CLI | Complete |
| 6 | [Interactive CLI onboarding wizard](../archive/phase-6-interactive-cli-onboarding-wizard.md) | `escape-ai` menu and non-interactive subcommands for onboarding | Complete |
| 7 | [Planning and workflow generation](../archive/phase-7-planning-and-workflow-generation.md) | `plan draft/answer/apply/status`, single- and multi-repository task graph generation | Complete |
| 8 | [Integrated execution lifecycle](../archive/phase-8-execution-lifecycle-wiring.md) | Approved task graphs actually execute through the scheduler/runtime, with retry, checkpoint promotion, and cross-repository resume views | Complete |
| 9 | [`.esc-ai/` generated-artifact consolidation](../archive/escape-ai-directory-consolidation.md) | `repository_status`'s `INSTRUCTIONS.md` presence check and CLI rendering tests updated for the execution framework's new all-under-`.esc-ai/` manifest/index layout | Complete |
