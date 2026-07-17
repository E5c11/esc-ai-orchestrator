# ESC AI Orchestrator — Instructions

This service is a consumer of `esc-ai-execution-framework`. Portable task, run, event,
artifact, checkpoint, workspace, adapter, and policy definitions remain owned by that
framework. Do not duplicate or silently extend those contracts here.

The orchestrator owns control-plane concerns: persistence, scheduling, runtime
selection, workspace lifecycle, approvals, credentials, concurrency, observation, and
deployment. Keep provider-specific behavior behind runtime adapters.

The current SQLite/in-process implementation is a bootstrap, not a permanent scaling
decision. Preserve the store, scheduler, and runtime boundaries when extending it.
