from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from esc_exec.adapters import detect_build_system
from esc_exec.checkpoints import create_checkpoint, checkpoint_path, update_checkpoint
from esc_exec.claude_code_adapter import (
    ClaudeCodeClient, ClaudeCodeError, claude_auth_status, claude_cli_available, suggest_onboarding_answers,
)
from esc_exec.codex_adapter import codex_auth_status, codex_cli_available
from esc_exec.conversation import compact_conversation, run_turn
from esc_exec.dependencies import validate_dependency_graph
from esc_exec.indexing import validate_indexes
from esc_exec.manifests import overall_exit_code, validate_repository
from esc_exec.measurement import process_metrics
from esc_exec.model import ValidationResult
from esc_exec.onboarding import analyze_repository, apply_onboarding_answers
from esc_exec.planning import (
    WORK_TYPES, generate_multi_repository_workflow, generate_single_repository_workflow,
    planning_questions, route_objective,
)
from esc_exec.registry import (
    KNOWN_PROVIDERS, SUBSCRIPTION_CAPABLE_PROVIDERS, active_provider, add_route,
    default_registry_path, read_registry, resolve_route, set_provider,
)
from esc_exec.roadmap import load_project_roadmap, save_project_roadmap
from esc_exec.yaml_io import load_yaml

from esc_orchestrator.runtime import ClaudeCodeRuntime, CodexRuntime, OpenCodeRuntime
from esc_orchestrator.scheduler import Scheduler
from esc_orchestrator.store import Store


MENU = [
    "Onboard a repository",
    "Plan new work",
    "Resume active work",
    "Observe a run",
    "Configure system",
    "Validate the system",
]

NOT_YET_IMPLEMENTED = (
    "Not yet implemented -- this is a later phase of the Escape AI plan "
    "(see plan/cohesive-system-integration-and-onboarding.md). Only repository "
    "onboarding, planning new work, and resuming active work are wired up so far."
)

DEFAULT_OPENCODE_SERVER = "http://127.0.0.1:4097"


# ---------------------------------------------------------------------------
# Pure rendering (no I/O) -- tested independently of the business logic below.
# ---------------------------------------------------------------------------

BANNER = "\n".join([
    "╔════════════════════════════════════════════════════════════════════╗",
    "║                                                                    ║",
    "║   ███████ ███████  ██████  █████  ██████  ███████     █████  ██    ║",
    "║   ██      ██      ██      ██   ██ ██   ██ ██         ██   ██ ██    ║",
    "║   █████   ███████ ██      ███████ ██████  █████      ███████ ██    ║",
    "║   ██           ██ ██      ██   ██ ██      ██         ██   ██ ██    ║",
    "║   ███████ ███████  ██████ ██   ██ ██      ███████    ██   ██ ██    ║",
    "║                                                                    ║",
    "╠════════════════════════════════════════════════════════════════════╣",
    "║               Escape drift. Engineer consistency.                  ║",
    "╚════════════════════════════════════════════════════════════════════╝",
])


def render_menu() -> str:
    return "\n".join([BANNER, "", "What would you like to do?"])


def render_menu_options(options: list[str]) -> str:
    return "\n".join(f"  {i}. {option}" for i, option in enumerate(options, 1))


def render_proposal(proposal: dict[str, Any]) -> str:
    lines = [
        f"Repository: {proposal['repository']['id']} ({proposal['repository']['type']})",
        "",
        "Files:",
    ]
    for entry in proposal["files"]:
        lines.append(f"  [{entry['action']:<9}] {entry['path']} -- {entry['evidence']}")

    suggestions = proposal.get("profile_id_suggestions") or {}
    if suggestions:
        lines += ["", "Suggested architecture.profile_ids:"]
        lines += [f"  {component_id}: {', '.join(ids)}" for component_id, ids in suggestions.items()]

    existing = proposal.get("existing_adoption") or {}
    present = [key for key, value in existing.items() if value]
    if present:
        lines += ["", "Existing adoption detected:"] + [f"  - {key}" for key in present]

    questions = proposal.get("semantic_questions") or []
    lines.append("")
    lines.append(
        f"{len(questions)} question(s) require your input before this can be applied."
        if questions else "No outstanding questions -- ready to apply."
    )
    return "\n".join(lines)


def render_apply_result(result: dict[str, Any]) -> str:
    lines = ["Applied. Files written or updated:"]
    lines += [f"  {path}" for path in result.get("written", [])]

    inheritance = result.get("workflow_inheritance") or {}
    if inheritance.get("created"):
        lines += ["Workflow inheritance files created:"] + [f"  {path}" for path in inheritance["created"]]
    if inheritance.get("existing"):
        lines += [
            "Existing workflow files left untouched (review these yourself):",
        ] + [f"  {path}" for path in inheritance["existing"]]

    if result.get("stub_documents"):
        lines += ["", "WARNING -- stub architecture documents referenced (not yet complete):"]
        lines += [f"  {component_id}: {', '.join(ids)}" for component_id, ids in result["stub_documents"].items()]

    if result.get("missing_documents"):
        lines += ["", "WARNING -- referenced architecture documents could not be resolved:"]
        lines += [f"  {component_id}: {', '.join(ids)}" for component_id, ids in result["missing_documents"].items()]

    if result.get("empty_profile_id_suggestions"):
        lines += ["", "No architecture.profile_ids could be suggested for: " + ", ".join(result["empty_profile_id_suggestions"])]

    lines += ["", "Nothing has been committed. Review the files above, then commit them yourself."]
    return "\n".join(lines)


def render_status(info: dict[str, Any]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in info.items())


def render_validation(results: list[ValidationResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"{result.state.value:<10} {result.path}")
        lines += [f"  - {message}" for message in result.messages]
    return "\n".join(lines)


def render_work_types() -> str:
    return "Work type:"


def render_plan_draft(draft: dict[str, Any]) -> str:
    lines = [
        f"Initiative: {draft['initiative_id']} ({draft['work_type']})",
        f"Objective: {draft['objective']}",
        "",
        "Repositories and routed components:",
    ]
    for repository_id in draft["repositories"]:
        matches = draft["routing"].get(repository_id, [])
        suggested = ", ".join(match["component_id"] for match in matches) or "no matches"
        lines.append(f"  {repository_id}: suggested [{suggested}]")
    lines += ["", f"{len(draft['questions'])} question(s) require your input before this can be applied."]
    return "\n".join(lines)


def render_plan_result(result: dict[str, Any]) -> str:
    lines = ["Planned. Files written:"]
    for repository_id, paths in result.items():
        lines.append(f"  {repository_id}:")
        lines += [f"    {path}" for path in paths]
    lines += ["", "Nothing has been committed. Review the files above, then commit them yourself."]
    return "\n".join(lines)


def render_active_work(items: list[dict[str, Any]]) -> str:
    if not items:
        return "No active work found across registered repositories."
    lines = ["Active work:"]
    for index, item in enumerate(items, 1):
        status = item["latest_run_status"] or "never run"
        checkpoint = " [checkpoint candidate pending review]" if item["checkpoint_present"] else ""
        lines.append(
            f"  {index}. {item['repository_id']}/{item['task_id']} -- {status}, "
            f"{item['attempts']} attempt(s){checkpoint}"
        )
        lines.append(f"     {item['objective']}")
    return "\n".join(lines)


def render_execution_preview(repository_id: str, task_id: str, task_document: dict[str, Any], provider: dict[str, Any] | None = None) -> str:
    task = task_document["task"]
    return "\n".join([
        f"About to execute {repository_id}/{task_id}",
        f"Objective: {task['objective']}",
        f"Components: {', '.join(task_document['scope']['components'])}",
        f"Provider: {provider['id']} ({provider['route']})" if provider else "Provider: none connected yet",
        "Workspace/policy: placeholder defaults (read-only policy) pending real "
        "Configure system support -- this is not a finished permission story.",
    ])


def render_execution_result(result: dict[str, Any]) -> str:
    lines = [
        f"Run {result['run_id']} (attempt {result['attempt']}): {result['status']}",
    ]
    if result.get("error"):
        lines.append(f"Error: {result['error']}")
    if result.get("output_path"):
        lines.append(f"Output: {result['output_path']}")
    return "\n".join(lines)


def render_checkpoint_candidate(candidate: dict[str, Any]) -> str:
    checkpoint, progress = candidate["checkpoint"], candidate["progress"]
    lines = [
        f"Checkpoint candidate from run {candidate['run_id']} -- status: {checkpoint['status']}",
        "Blockers:", *(f"  - {blocker}" for blocker in progress.get("blockers", [])),
        "Remaining:", *(f"  - {item}" for item in progress.get("remaining", [])),
    ]
    if progress.get("decisions"):
        lines += ["Decisions:", *(f"  - {decision}" for decision in progress["decisions"])]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Operations -- delegate to esc_exec/Store only, no prompts/printing. These are
# what the end-to-end test exercises against a real repository.
# ---------------------------------------------------------------------------

def resolve_repository(value: str, registry: Path) -> tuple[str, Path]:
    """Resolve `value` as a registered repository ID, or as a filesystem path --
    registering it under its detected repository ID if it isn't registered yet."""
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        path = candidate.resolve()
        repository_id, _, _ = detect_build_system(path)
        try:
            resolve_route(registry, "repositories", repository_id)
        except (KeyError, FileNotFoundError):
            add_route(registry, "repositories", repository_id, path)
        return repository_id, path
    return value, resolve_route(registry, "repositories", value)


def analyze(store: Store, registry: Path, repository_id: str, repository_path: Path) -> dict[str, Any]:
    proposal = analyze_repository(repository_path, registry)
    store.save_onboarding_proposal(repository_id, proposal)
    return proposal


def apply_answers(store: Store, registry: Path, repository_id: str, repository_path: Path, answers: dict[str, Any]) -> dict[str, Any]:
    record = store.get_onboarding_proposal(repository_id)
    if record is None:
        raise ValueError(f"no onboarding proposal for `{repository_id}`; analyze first")
    result = apply_onboarding_answers(repository_path, record["proposal"], answers, registry)
    store.save_onboarding_answers(repository_id, answers, result)
    return result


def onboarding_process_metrics(store: Store, repository_id: str) -> dict[str, Any] | None:
    """None until both a proposal and applied answers exist -- there is no elapsed
    time to report for an in-progress or never-started onboarding."""
    proposal_record = store.get_onboarding_proposal(repository_id)
    answers_record = store.get_onboarding_answers(repository_id)
    if proposal_record is None or answers_record is None:
        return None
    return process_metrics(
        "onboarding", repository_id,
        proposal_record["created_at"], answers_record["updated_at"],
        len(proposal_record["proposal"].get("semantic_questions", [])),
        len(answers_record["answers"]),
    )


def planning_process_metrics(store: Store, initiative_id: str) -> dict[str, Any] | None:
    draft_record = store.get_plan_draft(initiative_id)
    result_record = store.get_plan_result(initiative_id)
    if draft_record is None or result_record is None:
        return None
    return process_metrics(
        "planning", initiative_id,
        draft_record["created_at"], result_record["updated_at"],
        len(draft_record["questions"]), len(result_record["answers"]),
    )


def repository_status(store: Store, registry: Path, repository_id: str) -> dict[str, Any]:
    proposal_record = store.get_onboarding_proposal(repository_id)
    pending_record = store.get_pending_answers(repository_id)
    answers_record = store.get_onboarding_answers(repository_id)
    try:
        path = resolve_route(registry, "repositories", repository_id)
    except (KeyError, FileNotFoundError):
        path = None
    return {
        "repository_id": repository_id,
        "registered": path is not None,
        "has_proposal": proposal_record is not None,
        "has_pending_answers": pending_record is not None,
        "has_applied_answers": answers_record is not None,
        "instructions_file_present": bool(path and (path / ".esc-ai" / "INSTRUCTIONS.md").is_file()),
        "workflows_directory_present": bool(path and (path / ".esc-ai" / "workflows").is_dir()),
        "process_metrics": onboarding_process_metrics(store, repository_id),
    }


def validate_all(repository_path: Path, registry: Path) -> list[ValidationResult]:
    results = list(validate_repository(repository_path, registry))
    results += validate_indexes(repository_path)
    results.append(validate_dependency_graph(repository_path))
    return results


# ---------------------------------------------------------------------------
# Execution and resumption -- Phase 8. Workspace/adapter/policy defaults below are
# PLACEHOLDERS pending real "Configure system" support (not built yet, still a stub
# menu item). The policy default is deliberately conservative (read-only) -- never
# default to a permissive policy just because there's no configuration UI yet.
# ---------------------------------------------------------------------------

def default_workspace(repository_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workspace": {
            "id": f"workspace-{repository_id}-default", "kind": "local",
            "repository": repository_id, "isolation": "process",
        },
    }


def default_adapter(provider: dict[str, Any]) -> dict[str, Any]:
    """
    `provider` is the connected provider record from registry.active_provider --
    {"id": "claude"|"openai", "route": "subscription"|"api-key"}. The subscription
    route resolves to whichever first-party adapter that provider has (claude ->
    ClaudeCodeAdapter, openai -> CodexAdapter); every api-key route goes through
    OpenCode today. (gemini isn't currently offered at all -- see
    plan/reintroduce-gemini-provider.md -- but this function still falls back to
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


def default_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "policy": {
            "id": "default-readonly",
            "description": "Placeholder pending Configure system: read-only, no edit/execute/network.",
        },
        "permissions": {"read": "allow", "edit": "deny", "execute": "deny", "network": "deny", "external_paths": "deny"},
    }


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


def active_work(store: Store, registry: Path) -> list[dict[str, Any]]:
    """Read-only: every registered repository's `.esc-ai/workflows/active/*/task.yaml`,
    cross-referenced against this orchestrator's own run/attempt records. No writes."""
    catalog = read_registry(registry)
    items: list[dict[str, Any]] = []
    for repository_id, route in catalog.get("repositories", {}).items():
        repository_path = Path(route["path"])
        active_dir = repository_path / ".esc-ai" / "workflows" / "active"
        if not active_dir.is_dir():
            continue
        for task_dir in sorted(path for path in active_dir.iterdir() if path.is_dir()):
            task_path = task_dir / "task.yaml"
            if not task_path.is_file():
                continue
            task_document = load_yaml(task_path)
            task_id = task_document["task"]["id"]
            latest_run = store.get_latest_run_for_task(task_id)
            candidate_present = bool(
                latest_run and latest_run["status"] == "failed" and latest_run.get("output_path")
                and (Path(latest_run["output_path"]) / "checkpoint.yaml").is_file()
            )
            items.append({
                "repository_id": repository_id,
                "task_id": task_id,
                "objective": task_document["task"]["objective"],
                "attempts": store.get_attempt_count(task_id),
                "latest_run_status": latest_run["status"] if latest_run else None,
                "checkpoint_present": candidate_present,
            })
    return items


def execute_task(
    store: Store, registry: Path, repository_id: str, repository_path: Path, task_id: str, provider: dict[str, Any],
    runtime: Any = None, opencode_server: str = DEFAULT_OPENCODE_SERVER,
) -> dict[str, Any]:
    """
    Connects an approved, already-written task.yaml to real execution via the same
    Scheduler/Store the HTTP daemon uses -- submit, wait for the background worker to
    finish (queue.join()), then close. A CLI invocation is inherently one task at a
    time, so this reuses Scheduler's exact submit/execute/update_run sequence
    without needing a long-lived daemon around it.

    `provider` must be an already-connected provider record (see
    ensure_provider_configured) -- this function does not prompt or default one.
    """
    task_path = repository_path / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml"
    if not task_path.is_file():
        raise ValueError(f"no task.yaml found for `{task_id}` in `{repository_id}`; plan apply first")
    contracts = {
        "task": load_yaml(task_path),
        "workspace": default_workspace(repository_id),
        "adapter": default_adapter(provider),
        "policy": default_policy(),
    }
    attempt = store.record_attempt(task_id)
    scheduler = Scheduler(store, runtime or resolve_runtime(provider, registry, opencode_server), registry)
    try:
        _, run_id = scheduler.submit(contracts)
        scheduler.queue.join()
    finally:
        scheduler.close()
    run = store.get_run(run_id)
    return {
        "task_id": task_id, "run_id": run_id, "attempt": attempt,
        "status": run["status"], "error": run.get("error"), "output_path": run.get("output_path"),
    }


def checkpoint_candidate(store: Store, task_id: str) -> dict[str, Any]:
    run = store.get_latest_run_for_task(task_id)
    if run is None or run["status"] != "failed" or not run.get("output_path"):
        raise ValueError(f"no failed run with a checkpoint candidate for `{task_id}`")
    candidate_path = Path(run["output_path"]) / "checkpoint.yaml"
    if not candidate_path.is_file():
        raise ValueError(f"no checkpoint candidate found at {candidate_path}")
    return {"run_id": run["id"], **load_yaml(candidate_path)}


def promote_checkpoint(repository_path: Path, task_id: str, candidate: dict[str, Any]) -> Path:
    """Promotes a transient run-failure checkpoint candidate into the durable,
    committable location -- always after human review of `candidate`'s contents,
    never a blind copy triggered automatically on failure."""
    checkpoint, progress = candidate["checkpoint"], candidate["progress"]
    task_path = repository_path / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml"
    if not task_path.is_file():
        raise ValueError(f"no task.yaml for `{task_id}` to attach the checkpoint to")
    kwargs = dict(
        run_id=checkpoint.get("run_id"), status=checkpoint.get("status", "blocked"),
        completed=progress.get("completed"), decisions=progress.get("decisions"),
        remaining=progress.get("remaining"), blockers=progress.get("blockers"),
        artifacts=progress.get("artifacts"), last_event_sequence=progress.get("last_event_sequence"),
    )
    if checkpoint_path(repository_path, task_id).is_file():
        return update_checkpoint(repository_path, task_id, **kwargs)
    return create_checkpoint(repository_path, task_path, **kwargs)


def draft_plan(store: Store, registry: Path, initiative_id: str, work_type: str, objective: str, repository_values: list[str]) -> dict[str, Any]:
    if work_type not in WORK_TYPES:
        raise ValueError(f"work_type must be one of: {', '.join(WORK_TYPES)}")
    repositories: list[str] = []
    routing: dict[str, list[dict[str, Any]]] = {}
    matches_by_repo: dict[str, list] = {}
    for value in repository_values:
        repository_id, repository_path = resolve_repository(value, registry)
        repositories.append(repository_id)
        matches = route_objective(repository_path, objective)
        matches_by_repo[repository_id] = matches
        routing[repository_id] = [
            {"component_id": match.component_id, "score": match.score, "reasons": list(match.reasons)}
            for match in matches
        ]
    questions = planning_questions(matches_by_repo)
    store.save_plan_draft(initiative_id, work_type, objective, repositories, routing, questions)
    return {
        "initiative_id": initiative_id, "work_type": work_type, "objective": objective,
        "repositories": repositories, "routing": routing, "questions": questions,
    }


def apply_plan(store: Store, registry: Path, initiative_id: str, answers: dict[str, Any]) -> dict[str, Any]:
    """
    A single-repository plan writes one task directly; a multi-repository plan
    chains each repository's task to the previous one in the declared order (the
    plan's own worked example -- contracts -> backend -> mobile -- is exactly this
    shape) rather than asking for a full dependency graph through the CLI. Both
    paths validate every reference before writing anything (see
    generate_single_repository_workflow/generate_multi_repository_workflow).
    """
    draft = store.get_plan_draft(initiative_id)
    if draft is None:
        raise ValueError(f"no plan draft for `{initiative_id}`; draft first")
    repositories = draft["repositories"]
    components_by_repo = answers.get("components", {})
    completion_conditions = answers.get("completion_conditions", [])
    scope_boundary = answers.get("scope_boundary", "")
    rollout_needs = answers.get("rollout_needs", "")

    if len(repositories) == 1:
        repository_id = repositories[0]
        _, repository_path = resolve_repository(repository_id, registry)
        written = generate_single_repository_workflow(
            repository_path, repository_id, initiative_id, draft["objective"], draft["work_type"],
            components_by_repo.get(repository_id, []), scope_boundary, completion_conditions, rollout_needs,
        )
        result = {repository_id: [str(path.relative_to(repository_path)) for path in written]}
    else:
        tasks: dict[str, Any] = {}
        previous_task_ref: str | None = None
        for repository_id in repositories:
            task_id = f"{initiative_id}-{repository_id}"
            task: dict[str, Any] = {
                "task_id": task_id,
                "components": components_by_repo.get(repository_id, []),
                "scope_boundary": scope_boundary,
                "completion_conditions": completion_conditions,
                "rollout_needs": rollout_needs,
            }
            if previous_task_ref:
                task["depends_on"] = [previous_task_ref]
            tasks[repository_id] = task
            previous_task_ref = f"{repository_id}/{task_id}"
        written_paths = generate_multi_repository_workflow(registry, initiative_id, draft["objective"], draft["work_type"], tasks)
        result = {}
        for repository_id, paths in written_paths.items():
            _, repository_path = resolve_repository(repository_id, registry)
            result[repository_id] = [str(path.relative_to(repository_path)) for path in paths]

    store.save_plan_result(initiative_id, answers, result)
    return result


# ---------------------------------------------------------------------------
# Interactive wizard -- thin glue between prompts and the operations above.
# ---------------------------------------------------------------------------

def _isatty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


# System prompts/questions in cyan, what you type back in green -- so a scrolling
# conversation stays easy to follow at a glance, distinct from plain informational
# output (which stays uncolored). Disabled outright when not a real TTY -- piped/
# redirected output, log files, and every existing test all get plain text, exactly
# as before this was added.
_QUESTION_COLOR = "\033[36m"
_ANSWER_COLOR = "\033[32m"
_RESET_COLOR = "\033[0m"


def print_question(text: str) -> None:
    print(f"{_QUESTION_COLOR}{text}{_RESET_COLOR}" if _isatty() else text)


def ask(prompt: str) -> str:
    """
    input() wrapper that colors the prompt text and the typed answer differently.
    Falls back to plain `input(f"{prompt} ")` -- byte-identical to this codebase's
    previous behavior -- whenever not a TTY, so no test needs to change because of
    this: builtins.input mocking works exactly as it always has.
    """
    if not _isatty():
        return input(f"{prompt} ")
    sys.stdout.write(f"{_QUESTION_COLOR}{prompt}{_RESET_COLOR} {_ANSWER_COLOR}")
    sys.stdout.flush()
    try:
        return input()
    finally:
        sys.stdout.write(_RESET_COLOR)
        sys.stdout.flush()


def select_menu(title: str, options: list[str]) -> int | None:
    """
    Return the 0-based index of the chosen option, or None if the user backed out
    (Esc/q in the arrow-key picker; blank input, EOF, Ctrl-C, or an unrecognized
    number in the fallback). Both paths always print their own feedback -- callers
    never need to.

    Arrow-key navigable, rendered inline in the normal scrollback, in a real
    terminal. Falls back to the previous type-a-number-and-press-enter behavior
    whenever stdin/stdout isn't a real TTY (piped, redirected, or under test) --
    same options, same order, same meaning, just a different input mechanism. This
    is why every existing input()-mocking test keeps working unchanged: unittest's
    stdout/stdin are never a real TTY.

    Deliberately not curses: curses takes over the whole screen (alternate-screen
    buffer, full erase), which wipes out everything already printed above the
    prompt -- confirmed visually, not just in theory: a live screenshot showed a
    "Connect one now?" prompt with zero context above it, the entire conversation
    output gone. The inline picker below prints the title as a normal line (stays in
    scrollback permanently) and redraws only the option lines in place using ANSI
    cursor movement, the same technique tools like fzf/gum use for an inline picker.
    """
    if _isatty():
        try:
            return _select_menu_inline(title, options)
        except Exception:
            pass  # any failure (unsupported terminal, no termios, etc.) falls through
    print_question(title)
    options_text = render_menu_options(options)
    print(f"{_ANSWER_COLOR}{options_text}{_RESET_COLOR}" if _isatty() else options_text)
    try:
        choice = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return None
    if not choice:
        return None
    try:
        index = int(choice) - 1
    except ValueError:
        index = -1
    if not (0 <= index < len(options)):
        print("Unrecognized choice.")
        return None
    return index


_shown_menu_hint = False


def _select_menu_inline(title: str, options: list[str]) -> int | None:
    import termios
    import tty

    global _shown_menu_hint
    print_question(title)
    if not _shown_menu_hint:
        print("(up/down or j/k to move, enter to select, q/esc to cancel)")
        _shown_menu_hint = True
    current = 0
    width = shutil.get_terminal_size(fallback=(80, 24)).columns

    def render(first: bool) -> None:
        if not first:
            sys.stdout.write(f"\033[{len(options)}A")  # move cursor back up to the first option line
        for i, option in enumerate(options):
            marker = "●" if i == current else "○"  # solid dot chosen, outlined dot otherwise
            line = f"{marker} {option}"
            if len(line) > width - 1:
                # Truncate to guarantee exactly one physical terminal row per option.
                # Confirmed live: a line long enough to wrap breaks the "move up
                # len(options) rows" redraw math below, since it assumes one option =
                # one row -- the result was stacked, un-cleared duplicate renders on
                # every keypress instead of a clean in-place redraw.
                line = line[:max(width - 2, 1)] + "…"
            sys.stdout.write("\033[2K\r")  # clear only this line, not the screen
            sys.stdout.write(f"\033[7m{line}\033[0m\n" if i == current else f"{_ANSWER_COLOR}{line}{_RESET_COLOR}\n")
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    original_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        render(first=True)
        while True:
            char = sys.stdin.read(1)
            if char == "\x1b":
                rest = sys.stdin.read(1)
                if rest != "[":
                    return None  # plain Esc
                arrow = sys.stdin.read(1)
                if arrow == "A":
                    current = (current - 1) % len(options)
                    render(first=False)
                elif arrow == "B":
                    current = (current + 1) % len(options)
                    render(first=False)
            elif char == "k":
                current = (current - 1) % len(options)
                render(first=False)
            elif char == "j":
                current = (current + 1) % len(options)
                render(first=False)
            elif char in ("\r", "\n"):
                return current
            elif char == "q":
                return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original_settings)


def confirm(question: str) -> bool:
    """
    Yes/No confirmation via the same select_menu every other choice in this CLI uses
    -- arrow-key pick in a real terminal, numbered fallback otherwise -- instead of a
    separate typed "[y/N]" convention. A cancelled/backed-out/unrecognized choice
    always means No, matching every "[y/N]" confirmation this replaces (none of them
    defaulted to Yes).
    """
    return select_menu(question, ["Yes", "No"]) == 0


def run_interactive(store: Store, registry: Path) -> int:
    choice = select_menu(render_menu(), MENU)
    if choice is None:
        return 0
    if choice == 0:
        return run_onboarding_interactive(store, registry)
    if choice == 1:
        return run_planning_interactive(store, registry)
    if choice == 2:
        return run_resume_interactive(store, registry)
    print(NOT_YET_IMPLEMENTED)
    return 0


def suggest_answers_via_provider(
    registry: Path, repository_path: Path, purpose_component_ids: list[str], frameworks_component_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """
    Tier 2 of plan/onboarding-answer-detection-and-suggestion.md, scoped to the
    Claude Code (subscription) route only for now -- the one adapter actually built
    and verified live this session. Returns {} immediately, with zero cost, whenever
    no provider is connected or the connected one isn't claude/subscription; this is
    what keeps onboarding provider-optional (see native-cli-provider-adapters.md's
    "no wall at first run" decision) -- Tier 2 is pure enrichment on top, never a
    requirement. Covers both `purpose` and `frameworks`/`targets` in the same batched
    call -- originally purpose-only, extended after a user noticed only the first
    onboarding question ever came back with a suggestion.
    """
    provider = active_provider(registry)
    if provider is None or provider != {"id": "claude", "route": "subscription"}:
        return {}
    try:
        return suggest_onboarding_answers(ClaudeCodeClient(), repository_path, purpose_component_ids, frameworks_component_ids)
    except ClaudeCodeError:
        return {}


def _parse_frameworks_pairs(value: str) -> dict[str, str]:
    frameworks: dict[str, str] = {}
    for pair in value.split(","):
        pair = pair.strip()
        if ":" in pair:
            key, _, val = pair.partition(":")
            frameworks[key.strip()] = val.strip()
    return frameworks


def _ask_with_optional_suggestion(suggested_text: str | None) -> tuple[str, bool]:
    """
    Returns (typed_value, accepted_suggestion). `suggested_text` is None when there is
    no suggestion to offer for this field at all (falls back to a plain "> " prompt);
    an empty string is itself a valid, confident suggestion ("looked, found nothing"),
    shown as "(none found)" but still acceptable via a blank Enter. The caller decides
    what an accepted suggestion actually means (a string, a dict, a list, ...) -- this
    helper only handles the prompt/accept-or-override mechanics.
    """
    if suggested_text is None:
        return ask(">").strip(), False
    print(f"    Suggested: {suggested_text or '(none found)'}")
    value = ask("> (Enter to accept, or type to override)").strip()
    return value, not value


def _collect_answer(question: dict[str, Any], answers: dict[str, dict[str, Any]], suggestions: dict[str, dict[str, Any]] | None = None) -> None:
    component_id, field = question["component_id"], question["field"]
    bucket = answers.setdefault(component_id, {})
    suggestion = (suggestions or {}).get(component_id, {})
    print_question(question["prompt"])

    if field == "purpose":
        value, accepted = _ask_with_optional_suggestion(suggestion.get("purpose"))
        bucket["purpose"] = suggestion["purpose"] if accepted else value
        return

    if field == "frameworks":
        frameworks_suggestion = suggestion.get("frameworks")
        shown = ", ".join(f"{k}:{v}" for k, v in frameworks_suggestion.items()) if frameworks_suggestion is not None else None
        value, accepted = _ask_with_optional_suggestion(shown)
        frameworks = dict(frameworks_suggestion) if accepted else _parse_frameworks_pairs(value)
        bucket["frameworks"] = frameworks

        print_question("Which platforms does it target? (optional -- press Enter to skip)")
        print("    Example: ios, android, web")
        targets_suggestion = suggestion.get("targets")
        shown = ", ".join(targets_suggestion) if targets_suggestion is not None else None
        targets_raw, accepted = _ask_with_optional_suggestion(shown)
        targets = list(targets_suggestion) if accepted else [t.strip() for t in targets_raw.split(",") if t.strip()]
        bucket["targets"] = targets
        return

    bucket[field] = ask(">").strip()


def _unfinished_onboarding_label(store: Store, registry: Path, repository_id: str) -> str:
    """
    Deliberately short -- kept to just the repository ID and pending-question count,
    not the full absolute path (shown again once picked, via render_proposal). A live
    screenshot showed a longer id+path+count label wrapping onto a second terminal
    row, which broke the inline picker's redraw math; select_menu now truncates
    defensively too, but there's no reason to make truncation do the work here when a
    short label is more readable regardless of terminal width.
    """
    try:
        resolve_repository(repository_id, registry)
        warning = ""
    except (KeyError, FileNotFoundError, ValueError):
        warning = " -- path no longer resolvable"
    proposal_record = store.get_onboarding_proposal(repository_id)
    pending = len(proposal_record["proposal"].get("semantic_questions", [])) if proposal_record else 0
    return f"{repository_id} ({pending} question(s) pending){warning}"


def run_onboarding_interactive(store: Store, registry: Path) -> int:
    unfinished = store.list_unfinished_onboardings()
    raw: str | None = None
    if unfinished:
        options = [_unfinished_onboarding_label(store, registry, repository_id) for repository_id in unfinished]
        options.append("Start a new onboarding")
        choice = select_menu("You have unfinished onboarding(s) -- continue one, or start a new one?", options)
        if choice is None:
            return 0
        if choice < len(unfinished):
            raw = unfinished[choice]

    if raw is None:
        try:
            raw = ask("Repository path:").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled -- nothing was written.")
            return 0

    try:
        repository_id, repository_path = resolve_repository(raw, registry)
    except ValueError as exc:
        print(f"Could not detect a supported build system: {exc}")
        return 1

    existing_proposal = store.get_onboarding_proposal(repository_id)
    try:
        proposal = analyze(store, registry, repository_id, repository_path)
    except (OSError, ValueError) as exc:
        print(f"Analysis failed: {exc}")
        return 1

    if existing_proposal and existing_proposal["input_digest"] == proposal["input_digest"]:
        print(f"Found an existing onboarding proposal for `{repository_id}` with unchanged inputs -- resuming.")
        existing_answers = store.get_onboarding_answers(repository_id)
        if existing_answers is not None:
            print("This repository was already onboarded with these inputs.")
            print(render_apply_result(existing_answers["result"]))
            if not confirm("Re-run anyway?"):
                return 0
    elif existing_proposal:
        print(f"Repository inputs changed since the last analysis for `{repository_id}`; re-analyzed.")

    print(render_proposal(proposal))

    answers: dict[str, dict[str, Any]] = {}
    questions = proposal["semantic_questions"]
    purpose_component_ids = [question["component_id"] for question in questions if question["field"] == "purpose"]
    frameworks_component_ids = [question["component_id"] for question in questions if question["field"] == "frameworks"]
    ai_answerable_count = len(set(purpose_component_ids) | set(frameworks_component_ids))
    if ai_answerable_count and active_provider(registry) is None:
        print(f"\n{ai_answerable_count} component(s) have questions an AI provider can suggest answers for -- confirm or edit, instead of typing from scratch.")
        prompt_provider_setup_interactive(registry)
    if ai_answerable_count and active_provider(registry) is not None:
        print("Thinking... (reading source to suggest answers -- this can take a minute)")
    suggestions = suggest_answers_via_provider(registry, repository_path, purpose_component_ids, frameworks_component_ids)
    try:
        for index, question in enumerate(questions, 1):
            print()
            print_question(f"Question {index} of {len(questions)}:")
            _collect_answer(question, answers, suggestions)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written. The proposal is saved; resume anytime by running escape-ai again.")
        return 0

    if not confirm("Apply these answers and write manifests?"):
        print("Cancelled -- nothing was written. The proposal is saved; resume anytime by running escape-ai again.")
        return 0

    try:
        result = apply_answers(store, registry, repository_id, repository_path, answers)
    except (OSError, ValueError) as exc:
        print(f"Apply failed: {exc}")
        return 1
    print(render_apply_result(result))
    return 0


def run_planning_conversation_interactive(
    registry: Path, repository_path: Path, repository_id: str, initiative_id: str, objective: str,
) -> str | None:
    """
    Optional AI-assisted back-and-forth to think through scope before the static
    scope_boundary/completion_conditions/rollout_needs questions are asked --
    Consumer 1 of plan/ai-conversation-primitive.md, proven against planning
    refinement first since it's the more frequently used flow (over scaffolding a
    brand-new repository).

    Provider-optional by the same "no wall at first run" discipline as onboarding's
    Tier 2 suggestions (suggest_answers_via_provider): returns None immediately, at
    zero cost, whenever no Claude Code subscription provider is connected, or the
    human declines -- the static Q&A flow works standalone exactly as before this
    existed. Pure enrichment, never a requirement.

    Free-form turn-taking (plan's open question 2, resolved this session): the human
    types plain text each turn rather than picking from structured options --
    appropriate here since a planning conversation is inherently exploratory, unlike
    onboarding's fixed field set.

    Ends when the human sends a blank line, or when the hard context threshold is
    hit (forced stop, per the plan's two-tier design) -- either way, if a session was
    actually started, compact_conversation still runs so the conversation isn't just
    thrown away: it saves a conversation_summary unconditionally, and offers a
    roadmap_proposal for explicit human confirm before writing project_roadmap,
    matching this system's propose-then-confirm discipline everywhere else.

    Returns the AI's last message text (shown as context ahead of the scope
    question), or None if no conversation happened. Fails open throughout -- a
    provider/subprocess/parsing error anywhere in here skips or ends the
    conversation without blocking the rest of planning.
    """
    provider = active_provider(registry)
    if provider is None or provider != {"id": "claude", "route": "subscription"}:
        return None
    if not confirm("Talk through this plan with AI before answering the scope questions?"):
        return None

    client = ClaudeCodeClient()
    existing_roadmap = load_project_roadmap(repository_path)
    context = ""
    if existing_roadmap:
        roadmap = existing_roadmap.get("project_roadmap", {})
        context = (
            "For context, here's what's already known about this repository:\n"
            f"purpose: {roadmap.get('purpose')}\ncurrent_stage: {roadmap.get('current_stage')}\n"
            f"direction: {roadmap.get('direction')}\n\n"
        )
    prompt = (
        f"{context}I'm planning a change in this repository: {objective!r}. Help me think "
        "through scope, edge cases, and what \"done\" looks like before I answer some "
        "structured questions about it. Ask me one focused question at a time, and keep "
        "each response short."
    )

    print_question("Starting a conversation with AI about this plan -- send a blank line to finish.")
    session_id: str | None = None
    last_text: str | None = None
    turns = 0
    while True:
        turn = run_turn(client, repository_path, prompt, tools=["Read", "Grep", "Glob"], resume_session_id=session_id)
        turns += 1
        if turn["is_error"]:
            print(f"AI conversation failed ({turn['error_detail']}) -- continuing without it.")
            break
        session_id = turn["session_id"]
        last_text = turn["text"] or last_text
        print_question(turn["text"] or "(no response)")

        if turn["threshold"] == "hard":
            print("This conversation has used most of the model's context window -- wrapping up now.")
            break
        if turn["threshold"] == "soft":
            print("(This conversation is getting long -- consider wrapping up soon.)")

        try:
            reply = ask("You (blank line to finish):").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not reply:
            break
        prompt = reply

    if session_id is None:
        return last_text

    compaction = compact_conversation(
        client, repository_path, f"plan-{initiative_id}", session_id,
        purpose=f"plan {initiative_id}: {objective}", existing_roadmap=existing_roadmap,
    )
    proposal = compaction.get("roadmap_proposal")
    if proposal and confirm("Update this repository's saved roadmap with what was just discussed?"):
        prior_purpose = (existing_roadmap or {}).get("project_roadmap", {}).get("purpose", "")
        save_project_roadmap(
            repository_path, repository_id,
            proposal.get("purpose") or prior_purpose,
            proposal.get("current_stage") or "",
            proposal.get("direction") or "",
            proposal.get("durable_decisions") or [],
        )
        print("Roadmap updated.")
    return last_text


def run_planning_interactive(store: Store, registry: Path) -> int:
    choice = select_menu(render_work_types(), WORK_TYPES)
    if choice is None:
        return 0
    work_type = WORK_TYPES[choice]

    try:
        objective = ask("Objective:").strip()
        initiative_id = ask("Initiative/task ID (a short slug, e.g. feature-user-export):").strip()
        repos_raw = ask("Repositories (comma-separated IDs or paths):").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written.")
        return 0
    repository_values = [value.strip() for value in repos_raw.split(",") if value.strip()]

    try:
        draft = draft_plan(store, registry, initiative_id, work_type, objective, repository_values)
    except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
        print(f"Could not draft this plan: {exc}")
        return 1
    print(render_plan_draft(draft))

    if len(draft["repositories"]) == 1:
        try:
            repository_id, repository_path = resolve_repository(draft["repositories"][0], registry)
            run_planning_conversation_interactive(registry, repository_path, repository_id, initiative_id, objective)
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled the conversation -- continuing with the plan questions.")

    answers: dict[str, Any] = {}
    try:
        for question in draft["questions"]:
            if question["field"] == "components":
                repository_id = question["repository"]
                value = ask(question["prompt"]).strip()
                answers.setdefault("components", {})[repository_id] = [item.strip() for item in value.split(",") if item.strip()]
            elif question["field"] == "completion_conditions":
                value = ask(question["prompt"]).strip()
                answers["completion_conditions"] = [item.strip() for item in value.split(",") if item.strip()]
            else:
                answers[question["field"]] = ask(question["prompt"]).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written. The draft is saved; resume anytime by running escape-ai again.")
        return 0

    store.save_plan_pending_answers(initiative_id, answers)

    if not confirm("Generate workflow files from these answers?"):
        print("Cancelled -- nothing was written. The draft and answers are saved; resume anytime by running escape-ai again.")
        return 0

    try:
        result = apply_plan(store, registry, initiative_id, answers)
    except (OSError, ValueError) as exc:
        print(f"Apply failed: {exc}")
        return 1
    print(render_plan_result(result))
    return 0


def prompt_provider_setup_interactive(registry: Path) -> dict[str, Any] | None:
    """
    Lazy, triggered-by-first-use only -- called from run_resume_interactive's
    "Execute now" action when no provider is connected yet, never at first-run/menu
    display. Returns None (nothing written) on any cancel, decline, or invalid choice.
    """
    print("No AI provider connected yet.")
    if not confirm("Connect one now?"):
        return None

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


def _resume_item_label(item: dict[str, Any]) -> str:
    status = item["latest_run_status"] or "never run"
    checkpoint = " [checkpoint pending]" if item["checkpoint_present"] else ""
    return f"{item['repository_id']}/{item['task_id']} -- {status}, {item['attempts']} attempt(s){checkpoint} -- {item['objective']}"


def run_resume_interactive(store: Store, registry: Path) -> int:
    items = active_work(store, registry)
    if not items:
        print(render_active_work(items))
        return 0

    choice = select_menu("Active work -- select a task:", [_resume_item_label(item) for item in items])
    if choice is None:
        return 0
    selected = items[choice]
    repository_id, task_id = selected["repository_id"], selected["task_id"]
    _, repository_path = resolve_repository(repository_id, registry)

    action_choice = select_menu(f"{repository_id}/{task_id} -- choose an action:", ["Execute now", "Promote checkpoint candidate"])
    if action_choice is None:
        return 0

    if action_choice == 0:
        provider = active_provider(registry)
        if provider is None:
            provider = prompt_provider_setup_interactive(registry)
            if provider is None:
                print("Cancelled -- no provider connected.")
                return 0
        task_path = repository_path / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml"
        print(render_execution_preview(repository_id, task_id, load_yaml(task_path), provider))
        if not confirm("Execute this task now?"):
            print("Cancelled -- nothing was executed.")
            return 0
        result = execute_task(store, registry, repository_id, repository_path, task_id, provider)
        print(render_execution_result(result))
        return 0

    if action_choice == 1:
        try:
            candidate = checkpoint_candidate(store, task_id)
        except ValueError as exc:
            print(f"Cannot promote: {exc}")
            return 1
        print(render_checkpoint_candidate(candidate))
        if not confirm("Promote this checkpoint into the durable workflow?"):
            print("Cancelled -- nothing was promoted.")
            return 0
        path = promote_checkpoint(repository_path, task_id, candidate)
        print(f"Promoted checkpoint to {path}")
        return 0

    return 0


# ---------------------------------------------------------------------------
# Non-interactive subcommands.
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="escape-ai")
    parser.add_argument("--registry", type=Path, default=None, help="Override the machine-local system catalog path")
    parser.add_argument("--db", type=Path, default=Path(".orchestrator/orchestrator.db"), help="Orchestrator state database path")
    subcommands = parser.add_subparsers(dest="command")

    repository = subcommands.add_parser("repository", help="Onboard and manage repositories")
    repository_commands = repository.add_subparsers(dest="repository_command", required=True)

    add = repository_commands.add_parser("add")
    add.add_argument("id")
    add.add_argument("path", type=Path)

    analyze_cmd = repository_commands.add_parser("analyze")
    analyze_cmd.add_argument("repository")
    analyze_cmd.add_argument("--json", action="store_true")

    answer_cmd = repository_commands.add_parser("answer")
    answer_cmd.add_argument("repository")
    answer_cmd.add_argument("answers_file", type=Path)

    repository_commands.add_parser("apply").add_argument("repository")
    repository_commands.add_parser("validate").add_argument("repository")
    repository_commands.add_parser("status").add_argument("repository")

    plan = subcommands.add_parser("plan", help="Plan a feature/fix and generate workflow files")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)

    plan_draft = plan_commands.add_parser("draft")
    plan_draft.add_argument("initiative_id")
    plan_draft.add_argument("request_file", type=Path)

    plan_answer = plan_commands.add_parser("answer")
    plan_answer.add_argument("initiative_id")
    plan_answer.add_argument("answers_file", type=Path)

    plan_commands.add_parser("apply").add_argument("initiative_id")
    plan_commands.add_parser("status").add_argument("initiative_id")

    task = subcommands.add_parser("task", help="Execute a planned task and manage its checkpoints")
    task_commands = task.add_subparsers(dest="task_command", required=True)

    task_run = task_commands.add_parser("run")
    task_run.add_argument("repository")
    task_run.add_argument("task_id")
    task_run.add_argument("--yes", action="store_true", help="Actually execute; without this, preview only")
    task_run.add_argument("--opencode", default=DEFAULT_OPENCODE_SERVER)

    task_promote = task_commands.add_parser("promote-checkpoint")
    task_promote.add_argument("repository")
    task_promote.add_argument("task_id")
    task_promote.add_argument("--yes", action="store_true", help="Actually promote; without this, preview only")

    resume_cmd = subcommands.add_parser("resume", help="Show active work across registered repositories")
    resume_cmd.add_argument("--json", action="store_true")

    provider = subcommands.add_parser("provider", help="Connect an AI provider")
    provider_commands = provider.add_subparsers(dest="provider_command", required=True)

    provider_auth = provider_commands.add_parser("auth")
    provider_auth.add_argument("name", choices=list(KNOWN_PROVIDERS))
    provider_auth.add_argument(
        "--route", choices=["subscription", "api-key"], default=None,
        help="Defaults to subscription for claude, api-key for everyone else",
    )

    return parser


def _dispatch_repository(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.repository_command == "add":
        add_route(registry, "repositories", args.id, args.path)
        print(f"REGISTERED repository `{args.id}` -> {args.path.expanduser().resolve()}")
        return 0

    if args.repository_command == "analyze":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            proposal = analyze(store, registry, repository_id, repository_path)
        except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(json.dumps(proposal, indent=2) if args.json else render_proposal(proposal))
        return 0

    if args.repository_command == "answer":
        try:
            repository_id, _ = resolve_repository(args.repository, registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        store.save_pending_answers(repository_id, answers)
        print(f"STORED     answers for `{repository_id}`; run `escape-ai repository apply {repository_id}` to write them.")
        return 0

    if args.repository_command == "apply":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        pending = store.get_pending_answers(repository_id)
        if pending is None:
            print(f"INCOMPLETE no pending answers for `{repository_id}`; run `escape-ai repository answer` first.")
            return 2
        try:
            result = apply_answers(store, registry, repository_id, repository_path, pending["answers"])
        except (OSError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_apply_result(result))
        return 0

    if args.repository_command == "validate":
        try:
            _, repository_path = resolve_repository(args.repository, registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        results = validate_all(repository_path, registry)
        print(render_validation(results))
        return overall_exit_code(results)

    if args.repository_command == "status":
        try:
            repository_id, _ = resolve_repository(args.repository, registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_status(repository_status(store, registry, repository_id)))
        return 0

    return 1


def _dispatch_plan(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.plan_command == "draft":
        request = json.loads(args.request_file.read_text(encoding="utf-8"))
        try:
            draft = draft_plan(
                store, registry, args.initiative_id,
                request["work_type"], request["objective"], request["repositories"],
            )
        except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_plan_draft(draft))
        return 0

    if args.plan_command == "answer":
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        store.save_plan_pending_answers(args.initiative_id, answers)
        print(f"STORED     answers for `{args.initiative_id}`; run `escape-ai plan apply {args.initiative_id}` to write them.")
        return 0

    if args.plan_command == "apply":
        pending = store.get_plan_pending_answers(args.initiative_id)
        if pending is None:
            print(f"INCOMPLETE no pending answers for `{args.initiative_id}`; run `escape-ai plan answer` first.")
            return 2
        try:
            result = apply_plan(store, registry, args.initiative_id, pending["answers"])
        except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_plan_result(result))
        return 0

    if args.plan_command == "status":
        draft = store.get_plan_draft(args.initiative_id)
        pending = store.get_plan_pending_answers(args.initiative_id)
        result = store.get_plan_result(args.initiative_id)
        print(render_status({
            "initiative_id": args.initiative_id,
            "has_draft": draft is not None,
            "has_pending_answers": pending is not None,
            "has_result": result is not None,
            "process_metrics": planning_process_metrics(store, args.initiative_id),
        }))
        return 0

    return 1


def _dispatch_task(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.task_command == "run":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            task_path = repository_path / ".esc-ai" / "workflows" / "active" / args.task_id / "task.yaml"
            if not task_path.is_file():
                print(f"INVALID    no task.yaml found for `{args.task_id}` in `{repository_id}`")
                return 1
            task_document = load_yaml(task_path)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        provider = active_provider(registry)
        print(render_execution_preview(repository_id, args.task_id, task_document, provider))
        if not args.yes:
            print("Preview only -- re-run with --yes to actually execute.")
            return 0
        if provider is None:
            print("INCOMPLETE no AI provider connected; run `escape-ai provider auth <name>` first.")
            return 2
        result = execute_task(store, registry, repository_id, repository_path, args.task_id, provider, opencode_server=args.opencode)
        print(render_execution_result(result))
        return 0 if result["status"] == "succeeded" else 1

    if args.task_command == "promote-checkpoint":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            candidate = checkpoint_candidate(store, args.task_id)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_checkpoint_candidate(candidate))
        if not args.yes:
            print("Preview only -- re-run with --yes to actually promote.")
            return 0
        path = promote_checkpoint(repository_path, args.task_id, candidate)
        print(f"Promoted checkpoint to {path}")
        return 0

    return 1


def _dispatch_resume(args: argparse.Namespace, store: Store, registry: Path) -> int:
    items = active_work(store, registry)
    print(json.dumps(items, indent=2) if args.json else render_active_work(items))
    return 0


def _dispatch_provider(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.provider_command == "auth":
        route = args.route or ("subscription" if args.name in SUBSCRIPTION_CAPABLE_PROVIDERS else "api-key")
        try:
            connect_provider(registry, args.name, route)
        except ValueError as exc:
            print(f"INVALID    {exc}")
            return 1
        note = "" if route == "subscription" else (
            " -- routes through OpenCode; make sure it's configured for this provider (`opencode auth login`)."
        )
        print(f"CONNECTED  {args.name} ({route}){note}")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = args.registry or default_registry_path()
    store = Store(args.db)
    if args.command is None:
        return run_interactive(store, registry)
    if args.command == "repository":
        return _dispatch_repository(args, store, registry)
    if args.command == "plan":
        return _dispatch_plan(args, store, registry)
    if args.command == "task":
        return _dispatch_task(args, store, registry)
    if args.command == "resume":
        return _dispatch_resume(args, store, registry)
    if args.command == "provider":
        return _dispatch_provider(args, store, registry)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
