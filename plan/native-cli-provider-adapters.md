# Native CLI Provider Adapters — Plan

**Status:** Proposed
**Date:** 2026-07-18
**Objective:** Let escape-ai reach the providers people actually pay for (a Claude
subscription, a ChatGPT subscription) without forcing separate API billing through a
middle-layer tool, by adding thin adapters around each provider's own first-party
agentic CLI instead of routing everything through one broad abstraction.

## Why this is a separate plan

Same reasoning as the other standalone plans in this directory: a distinct concern
from the main plan's phase sequence, worth its own document. This one is specifically
about *which providers escape-ai can reach and how*, not about the orchestration flow
itself — the `Runtime`/adapter abstraction that makes this pluggable already exists
and doesn't need to change; this plan is about what gets plugged into it.

## What we found, and why it changes the plan

OpenCode is the only implemented adapter today (`OpenCodeAdapter` in
esc-ai-execution-framework), and it works, tested across Phases 1-8. But investigating
what it actually costs a user to connect a provider through it surfaced a real
mismatch with the target audience:

- OpenCode has its own separate credential store (`~/.local/share/opencode/auth.json`,
  populated via `opencode auth login`) — independent of whatever's already configured
  in Claude Code or Codex. Being logged into either of those does nothing for OpenCode.
- For Claude specifically, OpenCode's Anthropic provider is API-key-based. There's no
  evidence it can use a Claude Pro/Max subscription the way Claude Code's own CLI does.
  So a user with *only* a subscription — the audience this project is actually
  targeting, per earlier discussion — would need to go get a separate Anthropic API
  key, incurring separate per-token billing, just to reach Claude through OpenCode.
- Meanwhile, Claude Code's own headless mode (`claude -p`, non-`--bare`) already uses
  subscription auth natively when the user is logged in, with zero extra billing.
  Building a thin adapter around that existing tool gets subscription-aware access to
  Claude for free — no reimplementation, no separate credential setup.

The deeper realization: OpenCode's real value isn't "the way to reach Claude or GPT" —
it's a broad abstraction (75+ providers, including local models) with its own complete
agentic tool-execution loop, which is genuinely hard, valuable engineering. But
duplicating that same value for Claude and OpenAI specifically is pointless, because
Anthropic and OpenAI already ship their own first-party tools (Claude Code, Codex CLI)
that solve "subscription-aware auth plus a provider-perfect tool-execution loop"
natively, for free. Routing through OpenCode to reach them adds cost and friction
instead of removing it.

## Design

### The shape: more adapters, not a different architecture

The `Runtime` protocol (`execute(contracts) -> Path`) and `adapter.yaml`'s
`provider`/`kind`/`configuration.model` fields already exist specifically to make this
pluggable — nothing about escape-ai's design needs to change. What's missing is simply
more adapters:

- **Claude Code adapter** — shells out to `claude -p`, using `--resume`/`--continue`
  for session continuity and `--output-format json` for structured, parseable output.
  Subscription-aware by default (plain `-p`, not `--bare`).
- **Codex adapter** — the equivalent pattern for OpenAI/ChatGPT subscribers, *if*
  Codex CLI has an equivalent headless/scriptable mode with native subscription
  auth — this needs the same verification Claude Code's behavior got before any of it
  is assumed. Not yet confirmed.
- **OpenCode stays** as the catch-all adapter — it earns its place specifically for
  providers without their own first-party agentic CLI: local models via
  Ollama/LM Studio, Groq, Bedrock, Azure OpenAI, DeepSeek, the long tail. That's a real
  gap only a broad abstraction like OpenCode fills.

"Switch providers" and "fork tasks to different providers" both fall out of this for
free: a task's `adapter.yaml` already declares which adapter it uses, and a
multi-repository initiative's tasks can each declare a different one independently —
this is the shape the system already has, not a new mechanism.

### Two hard constraints on the Claude Code adapter specifically

1. **`--bare` mode is Claude Code's own recommended pattern for scripted/automation
   use, and `--bare` skips OAuth entirely, requiring an API key.** It's also "slated
   to become the default for `-p` in a future release" per Claude Code's docs. A
   subscription-aware adapter has to deliberately use plain `-p`, going against Claude
   Code's own stated best practice for automation — and that path isn't guaranteed to
   stay available as Claude Code evolves. This needs to be monitored, not assumed
   permanent.
2. **Claude Pro/Max subscription terms are explicit: individual use only.** "The
   moment your code routes someone else's request through your seat, stop using the
   subscription OAuth token and switch to an API key." Fine for the current target —
   one person running escape-ai locally against their own login. A hard wall on any
   future hosted/shared version of escape-ai, which would need each user's own
   credentials (their own local subscription login, or their own API key) — never a
   shared seat. Recorded here so it isn't rediscovered the hard way later.
3. Subscription usage is a shared pool across claude.ai web chat and Claude Code —
   heavy automated escape-ai runs consume the same monthly allowance as everything
   else the user does with their subscription. Worth surfacing to the user in escape-
   ai's own output (cost/usage visibility), not hidden.

### Permission mapping is per-adapter, not shared

`tools_for_policy` (built for OpenCode's specific tool-flag shape) does not transfer
directly — Claude Code has its own permission model (`--permission-mode`, allow/deny
patterns in settings). Each adapter needs its own translation from `policy.yaml`'s
coarse `read`/`edit`/`execute`/`network` categories to that provider tool's actual
permission surface, following the same deny-by-default discipline as the existing
OpenCode mapping, not assuming the mapping itself is reusable code.

## Non-goals

- Do not rebuild a from-scratch multi-provider client (raw Anthropic/OpenAI API calls
  plus a hand-rolled tool-execution loop). That duplicates what Claude Code, Codex
  CLI, and OpenCode already are, for no gain.
- Do not make OpenCode the only path, or the recommended default, for providers that
  have their own first-party agentic CLI — that's the exact friction this plan exists
  to remove.
- Do not build any of this before the current OpenCode-based workflow is validated
  end to end (Phase 10). Adding adapters is exactly the kind of thing the `Runtime`
  protocol is designed to make cheap to do later — it isn't blocking anything now.
- Do not let a future hosted/shared version of escape-ai route multiple users'
  requests through one person's subscription seat — see the individual-use constraint
  above.

## Open questions

1. Does Codex CLI have a headless/scriptable mode with native ChatGPT-subscription
   auth, analogous to Claude Code's plain `-p`? Unverified — needs the same level of
   confirmation Claude Code got before any Codex adapter work starts.
2. Is a Gemini CLI adapter worth a fourth option, given Google also ships a first-party
   agentic CLI? Not investigated yet.
3. Exact shape of each adapter's own policy-to-permission mapping — sketched as "needs
   one per adapter" here, not designed in detail.
4. How does escape-ai surface subscription usage/cost information to the user, given
   it shares a pool with the provider's own interactive usage?
