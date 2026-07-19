# MCP-Based External Context Adapters — Plan

**Status:** Proposed
**Date:** 2026-07-18
**Objective:** Let a task pull bounded, observable context from external systems (ticket
trackers, wikis, CI) via MCP, without turning Escape AI into a general business-process
automation platform.

## Why this is a separate plan

Same reasoning as
[`framework-contribution-and-extensibility.md`](./framework-contribution-and-extensibility.md):
a distinct concern from the main plan, worth its own document rather than folding in
half-formed, so it doesn't get lost or dilute the phase sequence it isn't part of.
General practice going forward: capture a substantial new idea as its own plan doc
rather than cramming it into whichever one is open at the time.

## What's in scope, what isn't

**In scope:** MCP as a bounded, task-scoped source of *external context* for a software
engineering task already in flight — an additional adapter kind, not a new product.
Pulling a linked ticket's acceptance criteria, a referenced wiki page, or CI status
into a task's context is the same shape of problem this system already solves for
architecture documents: bounded, evidence-driven, observable.

**Out of scope:** general business-process automation (the motivating example: an MCP
agent that scans email and produces invoices). None of this system's core concepts —
repository, manifest, build system, verification gate, architecture style, checkpoint —
transfer to that domain. It would need an entirely different framework built around
different primitives (data sources, business rules, output documents), not an
extension of this one. Scoping MCP support to "context for a software task" keeps it
a feature; scoping it to "general automation" would make it a different product.

## Design

### 1. A new adapter kind

Extend `adapter.schema.yaml`'s `kind` enum (currently `agent-runtime`, `build-system`,
`test-framework`, `reporter`, `source-control`) with `external-context` — an adapter
that connects to an MCP server, scoped to the specific tools/resources a task is
allowed to invoke. This reuses the existing task → adapters → policy execution
contract shape rather than inventing a parallel mechanism.

### 2. Permission granularity — and why this can't come before the enforcement fix

`policy.schema.yaml`'s `permissions.network: allow/ask/deny` is too coarse for MCP.
Two tools on the same server carry very different risk — reading a wiki page and
deleting a ticket might both live behind the same server's "network" permission today.
This needs per-server, per-tool granularity, roughly:

```yaml
permissions:
  mcp:
    <server-id>:
      tools:
        <tool-name>: allow | ask | deny
```

This depends on closing the policy-to-tool-grant enforcement gap flagged during Phase
1 (`READ_ONLY_TOOLS` in `opencode_adapter.py` is currently hardcoded, ignoring whatever
`policy.yaml` declares). Building MCP support on top of an unenforced permission model
would be worse than the current gap, not better — MCP's tool surface is more varied
and higher-stakes than the fixed categories that exist today. **Do not build MCP
support before that enforcement work lands.**

### 3. Boundedness

Matching the same discipline already applied to architecture documents and everything
else in this system:

- Explicitly requested by a specific task — not standing background access to an
  external system.
- Captured in the task context bundle the same way resolved architecture documents
  are attached to `routing.components` — observable, not a side channel.
- Every fetch (or denial, or failure) is a portable event, logged like any other run
  activity.
- A denied or failed fetch surfaces clearly and stops the relevant step; it never
  silently proceeds as if the context wasn't needed.

### 4. Configuration

MCP server registration (non-secret connection details — endpoint, which tools are
exposed) belongs in the "Configure system" menu (see the main plan's Unified CLI and
interface section) alongside provider and permission configuration, following the
same machine-local catalog pattern already established. Credentials remain a
`credentials.provider` name pointer, never a value stored inline — same rule as the
rest of the system.yaml catalog.

### 5. Concrete in-scope use cases

To keep this grounded rather than abstract, and to validate the design against later:

- Pull acceptance criteria from a linked ticket during the planning conversation.
- Pull a referenced wiki/doc page as extra context for a task.
- Check CI status via an MCP server before a final verification gate is marked
  complete.

## Non-goals

- Do not build this before the core software-engineering loop is validated against a
  real repository (Phase 10 of the main plan). This is a plan to think from, not a
  queue to implement from yet.
- Do not build this before the policy-to-tool-grant enforcement gap is closed (see
  Design, item 2).
- Do not extend scope toward general business-process automation. If a genuine need
  for that shows up later, it is a different project, not a feature of this one.

## Open questions

1. Exact shape of per-server/per-tool permission granularity in `policy.schema.yaml`
   — the sketch above is illustrative, not decided.
2. Are `external-context` adapters declared per-task, or does a repository have
   default MCP context sources a task can opt into?
3. Does this need its own contract schema, or does it fit inside the existing
   `adapter.schema.yaml`'s free-form `configuration` object?
