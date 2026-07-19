# Native CLI Provider Adapters — Plan

**Status:** Implemented
**Date:** 2026-07-18
**Revised:** 2026-07-18 — added the subscription/API-key dual-route design; OpenCode is
not being cut, its role is being narrowed to a specific, still-necessary path.
**Objective:** Let escape-ai reach the providers people actually pay for (a Claude
subscription, a ChatGPT subscription) without forcing separate API billing through a
middle-layer tool, by adding thin adapters around each provider's own first-party
agentic CLI as an *additional* route alongside the existing API-key route — the user
picks per provider, not the system.

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
- **OpenCode stays**, unconditionally — see the dual-route design below. It's not
  demoted to a fallback; it's the API-key route for every provider, plus the only
  route at all for providers without a first-party agentic CLI (local models via
  Ollama/LM Studio, Groq, Bedrock, Azure OpenAI, DeepSeek, the long tail).

"Switch providers" and "fork tasks to different providers" both fall out of this for
free: a task's `adapter.yaml` already declares which adapter it uses, and a
multi-repository initiative's tasks can each declare a different one independently —
this is the shape the system already has, not a new mechanism.

### Dual route: subscription vs. API key, chosen by the user, not the system

Cutting OpenCode entirely was considered and rejected — not because it's required
infrastructure, but because API-key access is a real, independently valuable route
that a first-party-CLI wrapper can't provide:

- **Subscription route** (Claude Code / Codex adapters): zero extra billing, but
  bound by the individual-use ToS constraint below, sharing a pool with the user's own
  interactive usage, and dependent on the provider's CLI keeping a subscription-auth
  headless mode available (see the `--bare` risk below — not guaranteed to last).
- **API-key route** (OpenCode): separate per-token billing, but no individual-use
  wall, no shared-pool contention with interactive usage, and — because OpenCode
  already implements 75+ providers — the same route also covers every provider that
  has no first-party CLI at all.

Neither route subsumes the other; they're for different situations (an individual
optimizing cost vs. someone who wants predictable, unshared, automation-safe access,
or simply has no subscription to begin with). So the design is: for a given provider
that has both options (Anthropic, OpenAI), the user picks the route — a per-provider
configuration choice, not a hardcoded default — and that choice determines which
adapter (`claude`/`codex` CLI wrapper, or OpenCode) a task's `adapter.yaml` resolves
to. Providers with no first-party CLI only ever have the OpenCode route, same as
today.

### Provider CLI surface

A consistent set of commands every adapter exposes uniformly, so this stays pluggable
instead of turning into N bespoke integrations:

- **`auth`** — connect a provider: run the subscription-vs-API-key choice (see above)
  and store the result.
- **`model`** — list/select which model to use for that provider (formalizes
  `adapter.yaml`'s existing `configuration.model` field into a real command instead of
  hand-edited YAML).
- **`usage`** — show remaining quota/budget against the subscription pool; surfaces the
  90%-threshold colored warning decided above.
- **`doctor`** — health-check that the adapter is actually usable right now (CLI
  installed, logged in, model reachable) — catches broken auth before a task commits to
  it, not mid-run.
- **`switch`** — change the active provider: as a new global default, or as an override
  when resuming a specific checkpointed task (ties into
  `task-orchestration-and-verification-loop.md`'s checkpoint/resume mechanism).
- **`permissions`** — preview what a task's `policy.yaml` actually translates to for a
  specific adapter, since that mapping is per-adapter, not shared code.
- **`help`** — list all of the above.

**Two different surfaces, two different formats, same underlying logic:**

- **Scripted CLI** (`escape-ai ...`, one-shot from a terminal/script) — bare-word
  subcommands via argparse, nested under a `provider` noun, following the exact
  convention already used everywhere else in this CLI (`repository add`, `plan draft`,
  `task run`): `escape-ai provider auth claude`, `escape-ai provider usage claude`, etc.
  Neither `--auth` (flags are for modifying a command's behavior, not the action itself)
  nor `/auth` (not idiomatic for a shell-invoked binary) fits this surface.
- **Interactive mode** (the `escape-ai` menu-driven REPL from Phase 6) — slash-commands
  (`/auth`, `/model`, `/switch`), mirroring the convention Claude Code's own interactive
  session already uses for the same concepts, since users coming from Claude Code
  already know this pattern. Not designed in detail yet (Phase 6 only implemented the
  "onboard a repository" menu item; the other menu items exist as stubs).

### Community-contributed adapters

The adapter interface (`Runtime.execute(contracts) -> Path` plus `adapter.yaml`) is
deliberately the same kind of open extension point the architecture framework already
has for patterns/architectures — a new adapter (Gemini CLI, Aider, a local model
wrapper, or a variant OpenCode configuration) should be addable as a self-contained
module without touching orchestrator core, and contributable back the same way a new
architecture pattern doc is. Not designed in detail yet (no adapter plugin/registration
contract exists), but it's a stated goal for the adapter interface's shape, not an
afterthought bolted on once the first two adapters exist.

### Constraints and verified facts on the Claude Code adapter specifically

0. **Verified 2026-07-18 (was previously an unverified assumption):** `claude -p` and
   the Claude Agent SDK do draw from the Pro/Max/Team/Enterprise subscription pool
   today, and Anthropic's own docs explicitly name this as a supported path for
   *third-party apps that authenticate with a user's Claude subscription through the
   Agent SDK* — this isn't just CLI-subprocess-wrapping working by accident, it's a
   recognized integration surface. That's the good news underneath this whole plan.
   The risk is not hypothetical, though: Anthropic announced on 2026-05-14 that this
   exact usage (`claude -p`, Agent SDK, third-party subscription auth) would move out
   of the subscription pool entirely on 2026-06-15, onto separate metered API billing
   — which would have killed subscription-backed automation outright. They paused it
   *on the day it was due to take effect*, stating "nothing has changed, for now," and
   said they're "working to update the plan to better support how users build with
   Claude subscriptions" — i.e. some change is still coming, just not scheduled. Given
   this already happened once this year, this needs active monitoring (a changelog/
   announcements watch, not a one-time check), and the Claude Code adapter should be
   built assuming the ground can move again with roughly a month's notice.
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
   **Decided 2026-07-19:** the `usage` command (see the provider CLI surface below)
   surfaces a colored warning (e.g. yellow/orange via ANSI codes) once consumption
   crosses 90% of the subscription/quota pool for that provider. That's the visibility
   half. The actual safeguard against several parallel tasks colliding on the same cap
   is decided in `task-orchestration-and-verification-loop.md`: at 90%, automatic
   dispatch of *new* tasks stops (already-running ones finish normally), draining to a
   clean point for a `switch` to another provider — described there since it's about
   task dispatch behavior, not this plan's concern; not duplicated here.

### Permission mapping is per-adapter, not shared

`tools_for_policy` (built for OpenCode's specific tool-flag shape) does not transfer
directly — Claude Code has its own permission model (`--permission-mode`, allow/deny
patterns in settings). Each adapter needs its own translation from `policy.yaml`'s
coarse `read`/`edit`/`execute`/`network` categories to that provider tool's actual
permission surface, following the same deny-by-default discipline as the existing
OpenCode mapping, not assuming the mapping itself is reusable code.

### Constraints and verified facts on the Codex adapter specifically

Verified live 2026-07-19 against `codex-cli 0.144.1`:

0. **Real, working headless mode, structurally different from Claude Code's.** Codex
   executes everything as actual shell commands under a sandbox policy
   (`/bin/bash -lc '...'` observed directly in a live run's `command_execution` item),
   not discrete typed tool calls (Read/Edit/Bash as separate grants). This means its
   permission surface is fundamentally coarser: `-s/--sandbox
   <read-only|workspace-write|danger-full-access>` cannot separately grant "edit
   without execute" or "execute without edit" the way `tools_for_policy` does for
   Claude Code/OpenCode — the moment either is allowed, both become possible, because
   edits happen via shell too. The adapter's policy mapping must say this plainly
   (`workspace-write` if either `edit` or `execute` is `allow`, else `read-only`;
   `danger-full-access` never auto-selected), not paper over the gap.
1. **`-a/--ask-for-approval` is a top-level flag, not an `exec` subcommand flag.**
   `codex exec -a never ...` fails ("unexpected argument '-a' found"); the working
   form is `codex -a never exec ...`. Confirmed by hitting the error live, not from
   docs.
2. **`--skip-git-repo-check` is required outside a git repository.** Real target
   repositories are always git repos, so this is a scratch-testing concern more than
   a real-usage one, but the adapter should still pass it — a repository that somehow
   isn't a git repo shouldn't hard-fail here.
3. **stdin must be explicitly closed, not left open/inherited.** Codex's docs say
   piped stdin gets appended as a `<stdin>` block alongside a positional prompt
   argument; leaving stdin open (inherited, not explicitly closed) makes the process
   hang indefinitely waiting for EOF rather than treating "nothing piped" as "no
   additional input." `subprocess.run(..., stdin=subprocess.DEVNULL)` is required.
4. **Structured output (`--json`) has a different event schema than Claude Code's**,
   and no `total_cost_usd` anywhere — only token counts (`input_tokens`,
   `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens`) in a
   `turn.completed` event's `usage` block. The final answer comes from the last
   `item.completed` event whose `item.type` is `"agent_message"`.

## Non-goals

- Do not rebuild a from-scratch multi-provider client (raw Anthropic/OpenAI API calls
  plus a hand-rolled tool-execution loop). That duplicates what Claude Code, Codex
  CLI, and OpenCode already are, for no gain.
- Do not make OpenCode the *only* path for providers that have their own first-party
  agentic CLI, and do not silently default every such provider to it — the whole point
  of the dual-route design is that the user picks. But do not remove OpenCode as *a*
  path either — the API-key route is independently useful, not just a stopgap.
- Do not build any of this before the current OpenCode-based workflow is validated
  end to end (Phase 10). Adding adapters is exactly the kind of thing the `Runtime`
  protocol is designed to make cheap to do later — it isn't blocking anything now.
- Do not let a future hosted/shared version of escape-ai route multiple users'
  requests through one person's subscription seat — see the individual-use constraint
  above.

## Open questions

1. **Resolved 2026-07-19, verified live.** Codex CLI (`codex-cli 0.144.1`) has a real
   headless mode: `codex exec`. Confirmed both locally (`codex login status` reports
   "Logged in using ChatGPT", not an API key) and in OpenAI's own docs (ChatGPT
   sign-in is the default auth path; a separate `--with-api-key`/`--with-access-token`
   path bills through the OpenAI Platform account instead). Live-verified end to end:
   `codex -a never exec -s read-only --skip-git-repo-check --json "<prompt>"` executes
   correctly, reading a file and returning its content via a real shell command
   (`/bin/bash -lc '...'`) inside the sandbox. See "Constraints and verified facts on
   the Codex adapter" below for the full detail — architecturally different from
   Claude Code in a real way (shell-command execution under a sandbox policy, not
   discrete typed tool calls), not just a drop-in swap.
2. **Investigated 2026-07-19 — parked, not a fourth option yet.** Google's
   agentic CLI is not "Gemini CLI" anymore — Gemini CLI stopped serving Google AI
   Pro/Ultra subscription and free-tier requests on 2026-06-18, replaced by
   **Antigravity CLI** (`agy`), confirmed via Google's own developer blog. `agy
   --print` does draw from subscription quota, not API-key billing — confirmed by
   a real quota-exhaustion error ("Individual quota reached. Please upgrade your
   subscription...") on one account, and by a successful non-error run on a second,
   unpaid account. But three independent live attempts (`--mode plan`, default mode,
   `--dangerously-skip-permissions`) all failed to execute the actual given prompt —
   `agy --print` consistently investigated its own CLI flags/config instead and
   answered a meta-question about itself, ending with "If you intended to perform a
   specific action... please let me know!" This is a reliability finding, not just an
   unverified gap: the auth/billing question is answered, but the CLI does not
   currently appear to reliably execute a given headless task at all (`agy` version
   1.1.4). Revisit only after that specific failure mode is understood or a newer
   version is confirmed to fix it — do not build an Antigravity adapter on top of
   this without re-verifying execution reliability specifically, not just auth.
3. Exact shape of each adapter's own policy-to-permission mapping — sketched as "needs
   one per adapter" here, not designed in detail.
4. How does escape-ai surface subscription usage/cost information to the user, given
   it shares a pool with the provider's own interactive usage?
5. **Resolved at the UX level, not yet implemented:** asked once, at first run,
   per provider — not a single global switch, since a user may have a subscription for
   one provider and only an API key for another. escape-ai walks through each known
   provider ("Anthropic: subscription or API key?", "OpenAI: subscription or API
   key?"), writes the answer into `system.yaml` (or a sibling config file) alongside
   the existing repository/framework catalog. Changing it later goes through a real
   command (e.g. `escape-ai provider configure`) that re-runs the same guided
   question per provider, rather than requiring hand-edited YAML as the only path
   (manual edits still work, since it's just a config file — the command is for
   discoverability, not a lock). Still open: exact file/schema location, and whether
   a task's `adapter.yaml` reads this at plan-apply time (resolved once, baked into
   the task) or at run time (resolved live, so a later config change affects
   already-planned tasks too).
6. What does a community adapter contribution actually need to provide/register to be
   discoverable (a manifest shape, a registration point in the routing/registry layer)
   — not designed at all yet, just named as a goal for the interface's shape.
