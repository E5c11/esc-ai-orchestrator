from __future__ import annotations

from typing import Any

# Named, built-in policy profiles a task can start from by default (see
# plan/done/configure-system-policy-profiles.md) -- a small, fixed, shipped set,
# not free-form user-authored YAML (see that plan's Non-goals): the actual gap
# being closed is "there was only ever one hardcoded choice," not "there's no way
# to author an arbitrary policy document" (hand-editing policy.yaml directly
# already covers that, same as system.yaml itself).
#
# "standard-autonomous" is exactly the grant this system has always defaulted to
# (previously the sole, hardcoded return value of a `default_policy()` function
# with this same id under its old name, "full-autonomy") -- see
# plan/done/pre-flight-consent-and-bounded-autonomy.md layers 1-2 for why a coarse
# category-level grant, not a fine-grained per-path/per-action schema, is the
# right shape: real tasks legitimately discover they need to touch code outside
# their declared component scope, and a strict allowlist would just block the
# move a task legitimately needs to make. Safety comes from layer 3
# (HARD_DENY_SETTINGS in claude_code_adapter.py) and layer 4 (disposable worktree
# isolation, default_workspace above), not from withholding categories up front.
# `external_paths` stays denied for every profile below (no per-call path-scoping
# mechanism exists yet). `--yes` on `task run` is the actual human consent gate,
# unchanged by which profile is active.
#
# "readonly-review" promotes esc-ai-execution-framework's own
# examples/contracts/policy.yaml from a schema-shape example to a real, selectable
# option -- for investigation/review tasks that should never write or reach the
# network.
POLICY_PROFILES: dict[str, dict[str, Any]] = {
    "standard-autonomous": {
        "schema_version": 1,
        "policy": {
            "id": "standard-autonomous",
            "description": (
                "Full read/edit/execute/network autonomy within this task, contained by "
                "the hard-deny list and disposable worktree isolation, not per-path scoping."
            ),
        },
        "permissions": {"read": "allow", "edit": "allow", "execute": "allow", "network": "allow", "external_paths": "deny"},
    },
    "readonly-review": {
        "schema_version": 1,
        "policy": {
            "id": "readonly-review",
            "description": "Permit repository inspection without mutation or external access.",
        },
        "permissions": {"read": "allow", "edit": "deny", "execute": "ask", "network": "deny", "external_paths": "deny"},
        "limits": {"max_parallel_agents": 1, "max_run_seconds": 900},
        "approvals": ["execute"],
    },
}


DEFAULT_POLICY_PROFILE_ID = "standard-autonomous"

