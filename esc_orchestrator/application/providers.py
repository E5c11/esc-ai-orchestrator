from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from esc_exec.claude_client import claude_auth_status, claude_cli_available
from esc_exec.codex_adapter import codex_auth_status, codex_cli_available
from esc_exec.registry import default_policy_id, set_provider
from esc_orchestrator.domain.policy_profiles import (
    DEFAULT_POLICY_PROFILE_ID,
    POLICY_PROFILES,
)
from esc_orchestrator.runtime import ClaudeCodeRuntime, CodexRuntime, OpenCodeRuntime

DEFAULT_OPENCODE_SERVER = "http://127.0.0.1:4097"


# ---------------------------------------------------------------------------
# Execution and resumption -- Phase 8. Workspace default below is a placeholder
# only in the sense that it's a fixed choice (kind: worktree), not that it's
# unfinished -- see its own docstring. Which policy profile a task starts from
# is real, named, and configurable (see POLICY_PROFILES/resolve_default_policy
# above and "Configure system" -> "Show / select default policy"); what remains
# a genuine gap is anything *beyond* the category-level grant every profile
# still shares -- external_paths scoping and budget/cost limits (see
# plan/done/pre-flight-consent-and-bounded-autonomy.md's still-open sub-
# questions in items 3 and 6) aren't enforced yet. The category-level grant
# itself is not a placeholder: per that plan's layers 1-4, a task is granted
# whatever categories its active profile allows outright (not fine-grained
# per-path/per-action -- the plan's own "Non-goals" rejected that shape after
# finding real tasks legitimately wander outside their declared component
# scope), contained by HARD_DENY_SETTINGS (claude_code_adapter.py) and
# disposable worktree isolation (default_workspace below), not by withholding
# categories up front. `--yes` remains the human consent gate every time.
# ---------------------------------------------------------------------------

def default_workspace(repository_id: str) -> dict[str, Any]:
    """
    `kind: worktree` -- see plan/future/pre-flight-consent-and-bounded-autonomy.md
    layer 4: the Claude Code adapter creates a disposable git worktree for the
    task rather than editing the live checkout directly, so an unanticipated
    change is contained and reviewable (via `task promote-checkpoint`) instead of
    needing to be prevented mid-run. Unlike the policy default just below, this
    isn't a "deliberately conservative placeholder" situation -- worktree
    isolation is strictly safer than `local`/`process` with no compatibility
    cost (see that plan doc's finding that host-level state like JDK/DB/
    credentials is unaffected by which worktree is active), so there's no
    reason to default to anything less here once the adapter actually supports
    it.
    """
    return {
        "schema_version": 1,
        "workspace": {
            "id": f"workspace-{repository_id}-default", "kind": "worktree",
            "repository": repository_id, "isolation": "filesystem",
        },
    }


def default_adapter(provider: dict[str, Any]) -> dict[str, Any]:
    """
    `provider` is the connected provider record from registry.active_provider --
    {"id": "claude"|"openai", "route": "subscription"|"api-key"}. The subscription
    route resolves to whichever first-party adapter that provider has (claude ->
    ClaudeCodeAdapter, openai -> CodexAdapter); every api-key route goes through
    OpenCode today. (gemini isn't currently offered at all -- see
    plan/future/reintroduce-gemini-provider.md -- but this function still falls back to
    OpenCode for any unrecognized id/route combination, matching KNOWN_PROVIDERS'
    deny-by-default discipline rather than assuming only claude/openai ever appear
    here.)
    """
    if provider["route"] == "subscription" and provider["id"] == "claude":
        return {
            "schema_version": 1,
            "adapter": {
                "id": "claude-code-claude", "kind": "agent-runtime", "provider": "claude-code",
                "capabilities": ["sessions", "events", "tools", "permissions"],
            },
        }
    if provider["route"] == "subscription" and provider["id"] == "openai":
        return {
            "schema_version": 1,
            "adapter": {
                "id": "codex-openai", "kind": "agent-runtime", "provider": "codex",
                "capabilities": ["sessions", "events", "tools", "permissions"],
            },
        }
    return {
        "schema_version": 1,
        "adapter": {
            "id": "opencode-default", "kind": "agent-runtime", "provider": "opencode",
            "capabilities": ["sessions", "events", "tools", "permissions"],
        },
    }


def resolve_runtime(provider: dict[str, Any], registry: Path, opencode_server: str) -> Any:
    if provider["route"] == "subscription" and provider["id"] == "claude":
        return ClaudeCodeRuntime(registry)
    if provider["route"] == "subscription" and provider["id"] == "openai":
        return CodexRuntime(registry)
    return OpenCodeRuntime(opencode_server, registry)


def resolve_default_policy(registry: Path) -> dict[str, Any]:
    """
    Reads the registry's configured default policy profile (see "Configure
    system" -> "Show / select default policy") and falls back to
    DEFAULT_POLICY_PROFILE_ID if none is configured, or if a configured id no
    longer names a known profile (e.g. an older escape-ai version's profile set
    once offered something this version doesn't) -- an installation with nothing
    configured behaves exactly as this system always has, no silent behavior
    change on upgrade. Returns a fresh copy every call: POLICY_PROFILES is the
    canonical in-memory definition and must never be mutated by a caller that
    embeds the result into a task's contracts.
    """
    profile_id = default_policy_id(registry)
    if profile_id not in POLICY_PROFILES:
        profile_id = DEFAULT_POLICY_PROFILE_ID
    return copy.deepcopy(POLICY_PROFILES[profile_id])


# Per-provider subscription-route CLI info: static data only (which binary, how to
# install it, how to interpret its auth-status output, what to tell someone who has
# neither yet). The actual availability/auth-status checks are looked up by name at
# call time (see _subscription_cli_available/_subscription_auth_status below), not
# bound here -- binding the functions directly into this dict would capture them at
# module-load time, which breaks monkeypatching `claude_cli_available` etc. in tests.
# Real install commands, verified live 2026-07-19 -- `npm install -g
# @anthropic-ai/claude-code` and `npm install -g @openai/codex` are each CLI's own
# documented/well-established install path, not guessed.
SUBSCRIPTION_CLI_INFO: dict[str, dict[str, Any]] = {
    "claude": {
        "binary": "claude",
        "is_logged_in": lambda status: bool(status) and status.get("loggedIn") is True,
        "install": "npm install -g @anthropic-ai/claude-code",
        "login_hint": "run `claude auth login`",
    },
    "openai": {
        "binary": "codex",
        "is_logged_in": lambda status: bool(status) and status.lower().startswith("logged in"),
        "install": "npm install -g @openai/codex",
        "login_hint": "run `codex login`",
    },
}


def _subscription_cli_available(provider_id: str) -> bool:
    if provider_id == "claude":
        return claude_cli_available()
    if provider_id == "openai":
        return codex_cli_available()
    return False


def _subscription_auth_status(provider_id: str) -> Any:
    if provider_id == "claude":
        return claude_auth_status()
    if provider_id == "openai":
        return codex_auth_status()
    return None


def connect_provider(registry: Path, provider_id: str, route: str) -> dict[str, Any]:
    """
    The one real write path for provider connection -- used by both the interactive
    lazy prompt and `escape-ai provider auth`. Onboarding and planning never call
    this; only task execution needs a connected provider (see native-cli-provider-
    adapters.md's per-provider, asked-once-at-first-use design -- there is
    deliberately no upfront "connect all your providers" wizard).

    For the subscription route this is a real three-step confirm, not just a PATH
    check: CLI installed -> CLI actually logged in (via each provider's own auth-
    status command, not just presence) -> only then is the connection recorded.
    """
    if route == "subscription":
        info = SUBSCRIPTION_CLI_INFO.get(provider_id)
        if info is None:
            raise ValueError(f"`{provider_id}` has no subscription-route adapter yet; use route=api-key.")
        if not _subscription_cli_available(provider_id):
            raise ValueError(
                f"`{info['binary']}` CLI not found on PATH. Install it first: {info['install']}"
                " -- or connect with route=api-key instead."
            )
        status = _subscription_auth_status(provider_id)
        if not info["is_logged_in"](status):
            raise ValueError(
                f"`{info['binary']}` is installed but not logged in yet -- {info['login_hint']}, then try again."
            )
    set_provider(registry, provider_id, route)
    return {"id": provider_id, "route": route}

