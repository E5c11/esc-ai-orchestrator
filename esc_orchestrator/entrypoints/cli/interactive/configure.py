from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.registry import (
    KNOWN_PROVIDERS,
    SUBSCRIPTION_CAPABLE_PROVIDERS,
    active_provider,
    default_policy_id,
    set_default_policy,
)
from esc_exec.roadmap import load_project_roadmap, save_project_roadmap
from esc_orchestrator.application.providers import (
    SUBSCRIPTION_CLI_INFO,
    connect_provider,
)
from esc_orchestrator.application.repositories import (
    registered_repository_ids,
    resolve_repository,
)
from esc_orchestrator.domain.policy_profiles import POLICY_PROFILES
from esc_orchestrator.entrypoints.cli.render import (
    render_policy_status,
    render_provider_status,
    render_repository_list,
    render_roadmap,
)
from esc_orchestrator.entrypoints.cli.terminal import ask, confirm, select_menu


def _pick_and_connect_provider_interactive(registry: Path) -> dict[str, Any] | None:
    """
    Shared provider-picker/connector core -- pick a provider, pick a route (if
    subscription-capable), connect it. Returns None on any cancel/decline/invalid
    choice or a failed connection attempt. Callers own their own leading copy/confirm
    question: the lazy first-use prompt (`prompt_provider_setup_interactive`) and
    "Configure system"'s explicit connect/switch action
    (`configure_provider_interactive`) have different framing for the same mechanics.
    """
    provider_choice = select_menu("Providers:", list(KNOWN_PROVIDERS))
    if provider_choice is None:
        return None
    provider_id = KNOWN_PROVIDERS[provider_choice]

    if provider_id in SUBSCRIPTION_CAPABLE_PROVIDERS:
        cli_binary = SUBSCRIPTION_CLI_INFO[provider_id]["binary"]
        route_choice = select_menu(
            "Route:", [f"Subscription (uses your existing `{cli_binary}` login)", "API key (via OpenCode)"],
        )
        if route_choice is None:
            return None
        route = ("subscription", "api-key")[route_choice]
    else:
        route = "api-key"

    if route == "api-key":
        print(
            f"`{provider_id}` routes through OpenCode for the API-key route. Make sure OpenCode itself is "
            "configured for this provider (`opencode auth login`); escape-ai does not manage that."
        )

    try:
        provider = connect_provider(registry, provider_id, route)
    except ValueError as exc:
        print(f"Could not connect: {exc}")
        return None
    print(f"Connected `{provider_id}` ({route}).")
    return provider


def prompt_provider_setup_interactive(registry: Path) -> dict[str, Any] | None:
    """
    Lazy, triggered-by-first-use only -- called from run_resume_interactive's
    "Execute now" action when no provider is connected yet, never at first-run/menu
    display. Returns None (nothing written) on any cancel, decline, or invalid choice.
    """
    print("No AI provider connected yet.")
    if not confirm("Connect one now?"):
        return None
    return _pick_and_connect_provider_interactive(registry)


def configure_provider_interactive(registry: Path) -> None:
    """
    "Configure system" -> "Connect / switch provider" -- unlike the lazy first-use
    prompt above, this is reachable whether or not a provider is already connected,
    so it leads with the current status rather than assuming none exists.
    """
    current = active_provider(registry)
    print(render_provider_status(current))
    if not confirm("Connect a different provider?" if current else "Connect a provider now?"):
        return
    _pick_and_connect_provider_interactive(registry)


def configure_policy_interactive(registry: Path) -> None:
    """
    "Configure system" -> "Show / select default policy" -- same shape as
    configure_provider_interactive: leads with current status, then offers a
    change. Only the fixed POLICY_PROFILES set is offered (see that dict's own
    comment for why this doesn't build a free-form authoring form).
    """
    current = default_policy_id(registry)
    print(render_policy_status(current))
    if not confirm("Select a different default policy?" if current else "Select a default policy now?"):
        return
    profile_ids = list(POLICY_PROFILES)
    choice = select_menu(
        "Policy profiles:",
        [f"{profile_id} -- {POLICY_PROFILES[profile_id]['policy']['description']}" for profile_id in profile_ids],
    )
    if choice is None:
        return
    selected = profile_ids[choice]
    set_default_policy(registry, selected)
    print(f"Default policy set to `{selected}`.")


def configure_roadmap_interactive(registry: Path) -> None:
    """
    "Configure system" -> "Show / set project roadmap" -- a direct, human-only path
    to project_roadmap (.esc-ai/roadmap.yaml), independent of the AI-mediated
    planning conversation (run_planning_conversation_interactive) that's the only
    way to reach it otherwise -- see plan/done/project-vision-and-direction.md
    design 1. Reuses the same repository-picker pattern as "Observe a run". Q&A
    with blank-keeps-current-value, not a live conversation: a direction statement
    doesn't need AI mediation to be useful, and this path works with no provider
    connected at all.
    """
    repository_ids = registered_repository_ids(registry)
    if not repository_ids:
        print("No repositories registered yet.")
        return
    choice = select_menu("Select a repository:", repository_ids)
    if choice is None:
        return
    repository_id = repository_ids[choice]
    _, repository_path = resolve_repository(repository_id, registry)
    existing = load_project_roadmap(repository_path)
    print(render_roadmap(existing))
    if not confirm("Set/update this repository's roadmap now?"):
        return
    current = (existing or {}).get("project_roadmap", {})
    purpose = ask(f"Purpose [{current.get('purpose') or ''}]:").strip() or current.get("purpose", "")
    current_stage = ask(f"Current stage [{current.get('current_stage') or ''}]:").strip() or current.get("current_stage", "")
    direction = ask(f"Direction [{current.get('direction') or ''}]:").strip() or current.get("direction", "")
    existing_decisions = current.get("durable_decisions") or []
    decisions_raw = ask(f"Durable decisions, comma-separated [{', '.join(existing_decisions)}]:").strip()
    durable_decisions = (
        [item.strip() for item in decisions_raw.split(",") if item.strip()] if decisions_raw else existing_decisions
    )
    save_project_roadmap(repository_path, repository_id, purpose, current_stage, direction, durable_decisions)
    print("Roadmap updated.")


def run_configure_interactive(registry: Path) -> int:
    while True:
        choice = select_menu(
            "Configure system:",
            [
                "Show current provider", "Connect / switch provider", "List registered repositories",
                "Show / select default policy", "Show / set project roadmap", "Back",
            ],
        )
        if choice is None or choice == 5:
            return 0
        if choice == 0:
            print(render_provider_status(active_provider(registry)))
        elif choice == 1:
            configure_provider_interactive(registry)
        elif choice == 2:
            print(render_repository_list(registered_repository_ids(registry), registry))
        elif choice == 3:
            configure_policy_interactive(registry)
        elif choice == 4:
            configure_roadmap_interactive(registry)

