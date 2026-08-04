from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from esc_exec.adapters import detect_build_system
from esc_exec.architecture_lookup import load_architecture_index, resolve_architecture_docs
from esc_exec.checkpoints import create_checkpoint, checkpoint_path, update_checkpoint
from esc_exec.worktree import diff_summary, merge_worktree
from esc_exec.claude_code_adapter import (
    ClaudeCodeClient, ClaudeCodeError, claude_auth_status, claude_cli_available, granted_categories,
    suggest_architecture_coverage_gap, suggest_onboarding_answers, suggest_work_type_drift,
)
from esc_exec.codex_adapter import codex_auth_status, codex_cli_available
from esc_exec.conversation import (
    compact_conversation, run_turn, suggest_form_turn, suggest_groundable_answers_turn,
    suggest_unresolved_components,
)
from esc_exec.dependencies import validate_dependency_graph
from esc_exec.indexing import validate_indexes
from esc_exec.local_architecture import write_local_architecture_note
from esc_exec.manifests import component_manifest_path, overall_exit_code, repository_manifest_path, validate_repository
from esc_exec.measurement import process_metrics
from esc_exec.model import ManifestState, ValidationResult
from esc_exec.onboarding import ARCHITECTURE_FRAMEWORK_ID, analyze_repository, apply_onboarding_answers
from esc_exec.planning import (
    WORK_TYPES, architecture_doc_ids_for_components, generate_multi_repository_workflow,
    generate_single_repository_workflow, load_repository_index, planning_questions, route_objective,
)
from esc_exec.registry import (
    KNOWN_PROVIDERS, SUBSCRIPTION_CAPABLE_PROVIDERS, active_provider, add_route,
    default_policy_id, default_registry_path, read_registry, resolve_route,
    set_default_policy, set_provider,
)
from esc_exec.roadmap import load_project_roadmap, save_project_roadmap
from esc_exec.yaml_io import load_yaml

from esc_orchestrator.initiative import analyze_task_impact, find_ready_tasks
from esc_orchestrator.runtime import ClaudeCodeRuntime, CodexRuntime, OpenCodeRuntime, doctor_check
from esc_orchestrator.scaffold_wizards import render_wizard_suggestion
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

# plan/active/form-driven-planning-conversation.md -- only ever offered for
# single-repository plans (a form converging on one work_type/scope_boundary
# doesn't map cleanly onto per-repository differences in a multi-repo plan),
# so it's appended to the work-type menu conditionally, not a WORK_TYPES value.
CHAT_ABOUT_IT_OPTION = "Not sure -- let's chat about it"

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


def render_onboarding_map(repository_path: Path) -> str:
    """
    What onboarding actually produced, read directly from the generated
    manifests -- not just the list of file paths render_apply_result shows.
    This is "the map" -- what escape-ai now understands this repository to be,
    the same information Plan new work's routing and task execution actually
    read. Read from disk rather than passed in, so a user who hand-edits a
    manifest (still the only editing path today -- no in-tool editor, an
    explicit non-goal for now) always sees their real, current state, not a
    stale snapshot from the apply call that just ran.
    """
    repository = load_yaml(repository_manifest_path(repository_path))
    lines = [f"\nRepository map for `{repository['repository']['id']}` ({repository['repository']['type']}):"]
    for entry in repository.get("components", []):
        component_id = entry["id"]
        manifest = load_yaml(component_manifest_path(repository_path, component_id))
        component = manifest.get("component", {})
        purpose = component.get("purpose") or "(no purpose recorded)"
        profile_ids = manifest.get("architecture", {}).get("profile_ids") or []
        has_verification = bool(manifest.get("paths", {}).get("verification_profile"))
        lines.append(f"\n  {component_id}  ({component.get('path')}, {manifest.get('build', {}).get('system')})")
        lines.append(f"    purpose: {purpose}")
        lines.append(f"    architecture profiles: {', '.join(profile_ids) if profile_ids else '(none resolved)'}")
        lines.append(f"    verification gates: {'declared' if has_verification else 'not built for this build system yet'}")
    excluded = repository.get("excluded_components")
    if excluded:
        lines.append(f"\n  Excluded from onboarding: {', '.join(excluded)}")
    lines.append(
        "\nThese are real files under .esc-ai/, not a locked format -- edit purpose or "
        "architecture.profile_ids directly if something's wrong; re-running onboarding "
        "preserves hand-authored fields rather than overwriting them."
    )
    return "\n".join(lines)


def render_status(info: dict[str, Any]) -> str:
    return "\n".join(f"{key}: {value}" for key, value in info.items())


def render_validation(results: list[ValidationResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"{result.state.value:<10} {result.path}")
        lines += [f"  - {message}" for message in result.messages]
    return "\n".join(lines)


def render_system_validation(results: dict[str, list[ValidationResult] | str]) -> str:
    if not results:
        return "No registered repositories to validate."
    lines = []
    valid_count = 0
    for repository_id, value in results.items():
        if isinstance(value, str):
            lines.append(f"INVALID    {repository_id}: {value}")
            continue
        lines.append(f"{repository_id}:")
        lines.append(render_validation(value))
        if all(result.state == ManifestState.VALID for result in value):
            valid_count += 1
    lines += ["", f"{valid_count}/{len(results)} repositories fully valid."]
    return "\n".join(lines)


def render_provider_status(provider: dict[str, Any] | None) -> str:
    return f"Active provider: {provider['id']} ({provider['route']})" if provider else "No provider connected yet."


def render_policy_status(profile_id: str | None) -> str:
    """
    `profile_id` is the registry's raw configured value (may be None if unset, or
    a stale id no longer in POLICY_PROFILES) -- rendered plainly here rather than
    resolved, so a stale configured value is visible as itself rather than
    silently masked by resolve_default_policy's fallback.
    """
    if profile_id is None:
        return f"No default policy configured yet -- falls back to `{DEFAULT_POLICY_PROFILE_ID}`."
    if profile_id not in POLICY_PROFILES:
        return f"Configured default policy `{profile_id}` is not a known profile -- falls back to `{DEFAULT_POLICY_PROFILE_ID}`."
    return f"Default policy: `{profile_id}` -- {POLICY_PROFILES[profile_id]['policy']['description']}"


def render_roadmap(existing_roadmap: dict[str, Any] | None) -> str:
    """
    `existing_roadmap` is whatever load_project_roadmap returns -- the raw
    {"project_roadmap": {...}} document, or None if nothing has been saved yet.
    Shared by `roadmap show`, "Configure system" -> "Show / set project roadmap",
    and the pre-confirm diff in run_planning_conversation_interactive, so all three
    surfaces render this identically.
    """
    if not existing_roadmap:
        return "No roadmap set yet for this repository."
    roadmap = existing_roadmap.get("project_roadmap", {})
    decisions = roadmap.get("durable_decisions") or []
    lines = [
        f"Purpose: {roadmap.get('purpose') or '(none)'}",
        f"Current stage: {roadmap.get('current_stage') or '(none)'}",
        f"Direction: {roadmap.get('direction') or '(none)'}",
        f"Durable decisions: {', '.join(decisions) if decisions else '(none)'}",
    ]
    if roadmap.get("updated_at"):
        lines.append(f"Updated: {roadmap['updated_at']}")
    return "\n".join(lines)


def render_repository_list(repository_ids: list[str], registry: Path) -> str:
    if not repository_ids:
        return "No repositories registered yet."
    lines = ["Registered repositories:"]
    for repository_id in repository_ids:
        try:
            path = resolve_route(registry, "repositories", repository_id)
            lines.append(f"  {repository_id} -> {path}")
        except (KeyError, FileNotFoundError) as exc:
            lines.append(f"  {repository_id} -> UNRESOLVABLE ({exc})")
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
    dependency_questions = [question for question in draft["questions"] if question["field"] == "depends_on"]
    if dependency_questions:
        lines += ["", "Suggested dependency order (default if left unanswered):"]
        for question in dependency_questions:
            suggested_dependency = question.get("suggested") or "no dependencies"
            lines.append(f"  {question['repository']}: {suggested_dependency}")
    lines += ["", f"{len(draft['questions'])} question(s) require your input before this can be applied."]
    return "\n".join(lines)


def render_plan_result(result: dict[str, Any], dependency_graph: dict[str, list[str]] | None = None) -> str:
    """
    `dependency_graph` (plan/done/run-outcome-surfacing.md finding #7, generalized
    from a single chain to a real graph by plan/active/multi-repository-dependency-
    graph-planning.md) maps each repository to the other repositories its task
    depends on -- printed explicitly instead of only being visible by reading
    `task.yaml` by hand. None for a single-repository plan, where no dependency
    exists at all.
    """
    lines = ["Planned. Files written:"]
    for repository_id, paths in result.items():
        lines.append(f"  {repository_id}:")
        lines += [f"    {path}" for path in paths]
    if dependency_graph:
        lines += ["", "Dependency graph:"]
        for repository_id, dependencies in dependency_graph.items():
            lines.append(f"  {repository_id}: {'depends on ' + ', '.join(dependencies) if dependencies else 'no dependencies'}")
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


def render_execution_preview(
    repository_id: str, task_id: str, task_document: dict[str, Any], provider: dict[str, Any] | None = None,
    policy_document: dict[str, Any] | None = None, prior_consent_record: dict[str, Any] | None = None,
) -> str:
    """
    `policy_document`/`prior_consent_record` are optional -- callers that don't
    pass them (or the resume-menu preview, which reuses this for the same
    "about to execute" text) just get the older, scope-agnostic preview. When
    passed, this is the pre-flight consent step itself -- see
    plan/future/pre-flight-consent-and-bounded-autonomy.md layer 1: a task
    whose most recent run already recorded consent for the same categories
    gets a short "already consented" line instead of re-explaining the scope
    from scratch, but `--yes` is still required to actually dispatch every
    time regardless -- recording consent removes re-litigating *why* a task
    may touch these categories, not the separate, always-on "yes, run this
    specific attempt now" gate the rest of this CLI uses everywhere.
    """
    task = task_document["task"]
    lines = [
        f"About to execute {repository_id}/{task_id}",
        f"Objective: {task['objective']}",
        f"Components: {', '.join(task_document['scope']['components'])}",
        f"Provider: {provider['id']} ({provider['route']})" if provider else "Provider: none connected yet",
    ]
    if policy_document is not None:
        categories = granted_categories(policy_document)
        scope_text = ", ".join(categories) if categories else "read-only"
        already_consented = (
            prior_consent_record is not None
            and set(prior_consent_record.get("granted_categories", [])) == set(categories)
        )
        if already_consented:
            lines.append(f"Scope: {scope_text} (already consented on {prior_consent_record['granted_at']}).")
        else:
            lines.append(f"Scope: this run will be granted -- {scope_text}.")
    policy_id = policy_document.get("policy", {}).get("id") if policy_document is not None else None
    policy_label = f"`{policy_id}` -- " if policy_id else ""
    lines.append(
        f"Policy: {policy_label}full autonomy within the granted categories, contained by the "
        "hard-deny list and disposable worktree isolation (external_paths scoping "
        "and budget/cost limits are still unbuilt -- see plan/done/pre-flight-"
        "consent-and-bounded-autonomy.md)."
    )
    return "\n".join(lines)


def render_execution_result(result: dict[str, Any]) -> str:
    lines = [
        f"Run {result['run_id']} (attempt {result['attempt']}): {result['status']}",
    ]
    if result.get("error"):
        lines.append(f"Error: {result['error']}")
    if result.get("output_path"):
        lines.append(f"Output: {result['output_path']}")
    return "\n".join(lines)


def render_checkpoint_candidate(candidate: dict[str, Any], repository_path: Path | None = None) -> str:
    checkpoint, progress = candidate["checkpoint"], candidate["progress"]
    lines = [
        f"Checkpoint candidate from run {candidate['run_id']} -- status: {checkpoint['status']}",
        "Blockers:", *(f"  - {blocker}" for blocker in progress.get("blockers", [])),
        "Remaining:", *(f"  - {item}" for item in progress.get("remaining", [])),
    ]
    if progress.get("decisions"):
        lines += ["Decisions:", *(f"  - {decision}" for decision in progress["decisions"])]
    # See plan/future/pre-flight-consent-and-bounded-autonomy.md layer 4: the
    # worktree diff is the review step for anything the task touched, surfaced
    # in the same preview a human already gets before deciding --yes.
    if repository_path is not None:
        summary = diff_summary(repository_path, checkpoint["task_id"])
        if summary:
            lines += ["Worktree diff:", *(f"  {line}" for line in summary.splitlines())]
    return "\n".join(lines)


def render_run_detail(
    repository_id: str, task_id: str, detail: dict[str, Any], repository_path: Path | None = None,
) -> str:
    run = detail["run"]
    if run is None:
        return f"No runs yet for `{repository_id}/{task_id}`."
    lines = [
        f"{repository_id}/{task_id} -- run {run['id']}: {run['status']}",
        f"created {run['created_at']}, updated {run['updated_at']}",
    ]
    if run.get("error"):
        lines.append(f"Error: {run['error']}")
    if run.get("output_path"):
        lines.append(f"Output path: {run['output_path']}")

    lines += ["", "Events:"]
    events = detail["events"]
    lines += [f"  {event['sequence']}. {event['created_at']} {event['type']}" for event in events] if events else ["  (none)"]

    if detail["summary"] is not None:
        lines += ["", "Verification summary:", json.dumps(detail["summary"], indent=2)]

    if detail["checkpoint"] is not None:
        lines += ["", render_checkpoint_candidate(detail["checkpoint"], repository_path)]

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


def analyze(
    store: Store, registry: Path, repository_id: str, repository_path: Path,
    extra_resolved_components: dict[str, str] | None = None,
) -> dict[str, Any]:
    proposal = analyze_repository(repository_path, registry, extra_resolved_components)
    store.save_onboarding_proposal(repository_id, proposal)
    return proposal


def apply_answers(
    store: Store, registry: Path, repository_id: str, repository_path: Path, answers: dict[str, Any],
    resolved_components: dict[str, str] | None = None, excluded_component_ids: list[str] | None = None,
) -> dict[str, Any]:
    record = store.get_onboarding_proposal(repository_id)
    if record is None:
        raise ValueError(f"no onboarding proposal for `{repository_id}`; analyze first")
    result = apply_onboarding_answers(
        repository_path, record["proposal"], answers, registry, resolved_components, excluded_component_ids,
    )
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


def registered_repository_ids(registry: Path) -> list[str]:
    return sorted(read_registry(registry).get("repositories", {}))


def validate_system(registry: Path) -> dict[str, list[ValidationResult] | str]:
    """
    `validate_all` for every registered repository, keyed by repository ID -- the
    "Validate the system" menu item, which `repository validate <id>` (the existing
    per-repository command) has no equivalent of. A repository whose registered path
    no longer resolves (moved/deleted since registration) reports as a plain error
    string instead of raising -- one stale registration must not hide every other
    repository's real validation result.
    """
    results: dict[str, list[ValidationResult] | str] = {}
    for repository_id in registered_repository_ids(registry):
        try:
            repository_path = resolve_route(registry, "repositories", repository_id)
        except (KeyError, FileNotFoundError) as exc:
            results[repository_id] = str(exc)
            continue
        results[repository_id] = validate_all(repository_path, registry)
    return results


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
            # "waiting-approval" (layer 6: a permission denial, not a code
            # failure) gets a checkpoint candidate the same way "failed" does --
            # both are a human-reviewable blocker, just a different kind of one.
            candidate_present = bool(
                latest_run and latest_run["status"] in ("failed", "waiting-approval") and latest_run.get("output_path")
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


def prior_consent(store: Store, task_id: str) -> dict[str, Any] | None:
    """
    The most recent run's recorded `bindings.consent`, if any -- see
    plan/future/pre-flight-consent-and-bounded-autonomy.md layer 1. Only the
    latest run is consulted (Store doesn't expose full run history queried by
    task), which is sufficient: a task's consent history only matters for
    deciding whether *this* dispatch needs to re-explain scope, and the most
    recent attempt is always the relevant precedent for that. A task with no
    prior run, or whose most recent run's adapter never wrote a consent
    binding (e.g. a fake/legacy runtime in tests), returns None -- treated by
    render_execution_preview as "not yet consented," never as an error.
    """
    run = store.get_latest_run_for_task(task_id)
    if run is None:
        return None
    run_document = store.output_document(run["id"], "run.json")
    if not run_document:
        return None
    return run_document.get("bindings", {}).get("consent")


def run_detail(store: Store, task_id: str) -> dict[str, Any]:
    """
    "Observe a run" -- a read-only drill-down over a task's latest recorded run,
    for the "Observe a run" menu item. Every value read here already exists in
    `Store` and is already exposed once, over HTTP, by `api.py`'s `GET /runs/<id>`
    family; this just surfaces the same reads through `escape-ai` directly, since
    `execute_task` runs synchronously (the run named is always already finished by
    the time this is callable -- there is nothing to tail live here, only to
    review after the fact).

    `checkpoint`, when present, is wrapped with a top-level `run_id` the same way
    `checkpoint_candidate` already does, so `render_checkpoint_candidate` (built
    for `promote-checkpoint`) can be reused verbatim rather than duplicated.
    """
    run = store.get_latest_run_for_task(task_id)
    if run is None:
        return {"run": None, "events": [], "summary": None, "checkpoint": None}
    checkpoint_document = store.output_yaml(run["id"], "checkpoint.yaml")
    checkpoint = {"run_id": run["id"], **checkpoint_document} if checkpoint_document else None
    return {
        "run": run,
        "events": store.events(run["id"]),
        "summary": store.summary(run["id"]),
        "checkpoint": checkpoint,
    }


def _task_id_suggestions(repository_path: Path, task_id: str) -> list[str]:
    """
    Sibling task IDs under `.esc-ai/workflows/active/` for `repository_path`
    whose directory name starts with the given (likely wrong) `task_id` -- the
    documented multi-repo convention is always `<initiative-id>-<repository-id>`,
    so a plain prefix match against the initiative-id-only guess a user is
    likely to type covers the real dogfooding case directly (see
    plan/done/cli-discoverability.md finding #1). No fuzzy-matching library;
    scoped to this one repository, not a cross-repository search (see that
    plan's open question 1). Returns `[]` when the active workflows directory
    doesn't exist or nothing matches, so callers fall back to their own plain
    "not found" message unchanged.
    """
    active_dir = repository_path / ".esc-ai" / "workflows" / "active"
    if not active_dir.is_dir():
        return []
    return sorted(
        path.name for path in active_dir.iterdir()
        if path.is_dir() and path.name != task_id and path.name.startswith(task_id)
    )


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
        suggestions = _task_id_suggestions(repository_path, task_id)
        hint = f"did you mean: {', '.join(suggestions)}?" if suggestions else "plan apply first"
        raise ValueError(f"no task.yaml found for `{task_id}` in `{repository_id}`; {hint}")
    contracts = {
        "task": load_yaml(task_path),
        "workspace": default_workspace(repository_id),
        "adapter": default_adapter(provider),
        "policy": resolve_default_policy(registry),
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


def checkpoint_candidate(store: Store, repository_path: Path, task_id: str) -> dict[str, Any]:
    """
    A failed run's real checkpoint.yaml is the usual candidate. A *succeeded*
    run has no checkpoint.yaml at all (that file is only ever written on the
    failure/blocked path) -- but if it kept a worktree (a real diff worth
    reviewing, see plan/future/pre-flight-consent-and-bounded-autonomy.md
    layer 4), it still needs the same review-before-merge step, so one is
    synthesized here rather than requiring `promote-checkpoint` to grow a
    second, parallel command just to reach the same merge step.

    A `succeeded-no-changes` run (see plan/done/run-outcome-surfacing.md) gets
    the same synthesized-candidate treatment for the opposite reason: there's no
    worktree diff to merge, but a human still needs a clear "here's why this
    needs attention" surface instead of `promote-checkpoint` just raising "no
    checkpoint candidate found."
    """
    run = store.get_latest_run_for_task(task_id)
    if run is None or not run.get("output_path"):
        raise ValueError(f"no run with a checkpoint candidate for `{task_id}`")
    candidate_path = Path(run["output_path"]) / "checkpoint.yaml"
    if candidate_path.is_file():
        return {"run_id": run["id"], **load_yaml(candidate_path)}
    task_path = repository_path / ".esc-ai" / "workflows" / "active" / task_id / "task.yaml"
    objective = load_yaml(task_path)["task"]["objective"] if task_path.is_file() else task_id
    if run["status"] == "succeeded":
        run_document = store.output_document(run["id"], "run.json")
        worktree = (run_document or {}).get("bindings", {}).get("worktree")
        if worktree and worktree.get("kept"):
            return {
                "run_id": run["id"], "worktree_merge_only": True,
                "checkpoint": {"id": f"checkpoint-{task_id}", "task_id": task_id, "status": "ready-to-resume", "objective": objective},
                "progress": {
                    "completed": [], "decisions": [],
                    "remaining": ["Review the worktree diff below, then re-run with --yes to merge it."],
                    "blockers": [], "artifacts": [],
                },
            }
    if run["status"] == "succeeded-no-changes":
        return {
            "run_id": run["id"], "no_changes": True,
            "checkpoint": {"id": f"checkpoint-{task_id}", "task_id": task_id, "status": "ready-to-resume", "objective": objective},
            "progress": {
                "completed": [], "decisions": [],
                "remaining": [
                    "This run produced no changes and may need clarification or a different "
                    "approach -- review the run's own summary/artifact before deciding whether "
                    "to retry.",
                ],
                "blockers": [], "artifacts": [],
            },
        }
    raise ValueError(f"no checkpoint candidate found for `{task_id}`")


def promote_checkpoint(repository_path: Path, task_id: str, candidate: dict[str, Any]) -> Path | None:
    """Promotes a transient run-failure checkpoint candidate into the durable,
    committable location -- always after human review of `candidate`'s contents,
    never a blind copy triggered automatically on failure.

    `candidate["worktree_merge_only"]` (set only by checkpoint_candidate's
    synthesized succeeded-run case above) merges the task's worktree branch
    back and removes the worktree instead of writing a durable checkpoint --
    there's no real blocker to record, the merge is the whole point, so this
    returns None rather than a checkpoint path. A *failed* run's checkpoint
    never auto-merges here, even if it kept a worktree: independent
    verification already confirmed a succeeded run was clean (see
    task-orchestration-and-verification-loop.md's "trust the artifact, not the
    agent"), but a failed run's worktree may hold half-finished or broken
    edits -- merging those needs a human decision this function doesn't make
    for them. That worktree stays in place for manual inspection
    (`esc_exec.worktree.merge_worktree`/`remove_worktree` directly) until a
    dedicated resolution verb exists (see that plan doc's open question 5).

    `candidate["no_changes"]` (set only by checkpoint_candidate's synthesized
    succeeded-no-changes case) is the same "nothing to persist" shape as
    worktree_merge_only, just with nothing to merge either -- there's no
    worktree and no durable blocker, only a human having read the notice above.
    Returns None the same way."""
    if candidate.get("worktree_merge_only"):
        merge_worktree(repository_path, task_id)
        return None
    if candidate.get("no_changes"):
        return None
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


def apply_plan(
    store: Store, registry: Path, initiative_id: str, answers: dict[str, Any],
    local_architecture_notes_by_repo: dict[str, list[str]] | None = None,
) -> tuple[dict[str, Any], dict[str, list[str]] | None]:
    """
    A single-repository plan writes one task directly. A multi-repository plan
    writes one cross-linked task per repository, using each repository's own
    `answers["depends_on"]` entry (see plan/active/multi-repository-dependency-
    graph-planning.md) -- an arbitrary acyclic graph, not just a straight chain --
    when one was actually supplied; a repository missing from that dict (or the
    whole `depends_on` key missing entirely, e.g. an answers.json predating this
    field) falls back to depending on the repository immediately before it in
    declared order, exactly reproducing this function's original behavior. Both
    paths validate every reference before writing anything (see
    generate_single_repository_workflow/generate_multi_repository_workflow, which
    also rejects an unresolvable or cyclic depends_on graph).

    local_architecture_notes_by_repo (see
    offer_local_architecture_note_interactive) is keyed by repository_id, same
    shape as answers["components"] -- omitted entirely for the non-interactive
    CLI path, which never runs that check.

    Returns `(result, dependency_graph)` -- `dependency_graph` (plan/done/
    run-outcome-surfacing.md finding #7, generalized from a single chain to a real
    graph by the plan above) maps each repository to the list of other repository
    ids its task actually depends on, surfaced explicitly so `render_plan_result`
    can print it instead of leaving it only discoverable by reading `task.yaml`.
    None for a single-repository plan. `result` itself is unchanged in shape --
    only `store.save_plan_result` persists it, and that call site doesn't need the
    graph, which is fully re-derivable from the written task.yaml files at any time.
    """
    draft = store.get_plan_draft(initiative_id)
    if draft is None:
        raise ValueError(f"no plan draft for `{initiative_id}`; draft first")
    repositories = draft["repositories"]
    components_by_repo = answers.get("components", {})
    completion_conditions = answers.get("completion_conditions", [])
    scope_boundary = answers.get("scope_boundary", "")
    rollout_needs = answers.get("rollout_needs", "")
    notes_by_repo = local_architecture_notes_by_repo or {}

    if len(repositories) == 1:
        repository_id = repositories[0]
        _, repository_path = resolve_repository(repository_id, registry)
        written = generate_single_repository_workflow(
            repository_path, repository_id, initiative_id, draft["objective"], draft["work_type"],
            components_by_repo.get(repository_id, []), scope_boundary, completion_conditions, rollout_needs,
            local_architecture_notes=notes_by_repo.get(repository_id),
        )
        result = {repository_id: [str(path.relative_to(repository_path)) for path in written]}
        dependency_graph = None
    else:
        depends_on_answers = answers.get("depends_on")
        tasks: dict[str, Any] = {}
        dependency_graph = {}
        for index, repository_id in enumerate(repositories):
            previous_repository_id = repositories[index - 1] if index > 0 else None
            if depends_on_answers is not None and repository_id in depends_on_answers:
                dependency_repo_ids = depends_on_answers[repository_id]
            else:
                dependency_repo_ids = [previous_repository_id] if previous_repository_id else []
            dependency_graph[repository_id] = dependency_repo_ids

            task_id = f"{initiative_id}-{repository_id}"
            task: dict[str, Any] = {
                "task_id": task_id,
                "components": components_by_repo.get(repository_id, []),
                "scope_boundary": scope_boundary,
                "completion_conditions": completion_conditions,
                "rollout_needs": rollout_needs,
            }
            if dependency_repo_ids:
                task["depends_on"] = [f"{dep_repo}/{initiative_id}-{dep_repo}" for dep_repo in dependency_repo_ids]
            if notes_by_repo.get(repository_id):
                task["local_architecture_notes"] = notes_by_repo[repository_id]
            tasks[repository_id] = task
        written_paths = generate_multi_repository_workflow(registry, initiative_id, draft["objective"], draft["work_type"], tasks)
        result = {}
        for repository_id, paths in written_paths.items():
            _, repository_path = resolve_repository(repository_id, registry)
            result[repository_id] = [str(path.relative_to(repository_path)) for path in paths]

    store.save_plan_result(initiative_id, answers, result)
    return result, dependency_graph


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
    """
    Loops back to this same menu after every action -- including one that ends
    in an error message (a bad repository path, a failed apply, ...) -- instead
    of exiting the whole process. A single action's own return code was
    previously propagated straight out of main(), so completing (or even just
    failing) one onboarding silently ended the entire session; the only
    deliberate exit is backing out of the menu itself (Esc/blank/EOF/Ctrl-C,
    handled by select_menu returning None).
    """
    while True:
        choice = select_menu(render_menu(), MENU)
        if choice is None:
            return 0
        if choice == 0:
            run_onboarding_interactive(store, registry)
        elif choice == 1:
            run_planning_interactive(store, registry)
        elif choice == 2:
            run_resume_interactive(store, registry)
        elif choice == 3:
            run_observe_interactive(store, registry)
        elif choice == 4:
            run_configure_interactive(registry)
        elif choice == 5:
            print(render_system_validation(validate_system(registry)))


def suggest_answers_via_provider(
    registry: Path, repository_path: Path, purpose_component_ids: list[str], frameworks_component_ids: list[str],
    resume_session_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """
    Tier 2 of plan/done/onboarding-answer-detection-and-suggestion.md, scoped to the
    Claude Code (subscription) route only for now -- the one adapter actually built
    and verified live this session. Returns {} immediately, with zero cost, whenever
    no provider is connected or the connected one isn't claude/subscription; this is
    what keeps onboarding provider-optional (see native-cli-provider-adapters.md's
    "no wall at first run" decision) -- Tier 2 is pure enrichment on top, never a
    requirement. Covers both `purpose` and `frameworks`/`targets` in the same batched
    call -- originally purpose-only, extended after a user noticed only the first
    onboarding question ever came back with a suggestion.

    resume_session_id, when given, means a module-resolution turn already ran this
    session (see suggest_unresolved_components) -- this call becomes a `--resume`
    of that same session (suggest_groundable_answers_turn) instead of a fresh
    `client.ask()`, so it's the cheap second-turn cost rather than paying the
    ~40-50K-token fixed setup cost twice in one onboarding pass (see
    plan/active/generic-multi-component-detection.md design section 5).
    """
    provider = active_provider(registry)
    if provider is None or provider != {"id": "claude", "route": "subscription"}:
        return {}
    if resume_session_id:
        applicability = {"purpose": set(purpose_component_ids), "frameworks_targets": set(frameworks_component_ids)}
        result = suggest_groundable_answers_turn(
            ClaudeCodeClient(), repository_path, applicability, resume_session_id,
        )
        return result["suggestions"]
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


def confirm_work_type_drift_interactive(
    registry: Path, repository_path: Path, work_type: str, objective: str,
    scope_boundary: str, completion_conditions: list[str],
) -> str:
    """
    plan/active/planning-consistency-checks.md design section 1 -- runs once
    scope_boundary/completion_conditions are known (works identically regardless
    of whether the static question path or a future conversation path produced
    them, per that plan's own framing), checks whether the declared work_type
    still fits, and if drift is flagged, asks the human to either reclassify or
    explicitly stay bounded to the original -- never reclassifies silently,
    never blocks.

    Provider-gated the same "no wall" way as suggest_answers_via_provider --
    returns work_type unchanged, at zero cost, whenever no claude/subscription
    provider is connected. repository_path is only used as the subprocess's
    working directory (the check is text-only, grants zero tools -- see
    suggest_work_type_drift's docstring), so any resolvable repository from the
    plan works, including for a multi-repository plan.
    """
    provider = active_provider(registry)
    if provider != {"id": "claude", "route": "subscription"}:
        return work_type
    result = suggest_work_type_drift(
        ClaudeCodeClient(), repository_path, work_type, objective, scope_boundary, completion_conditions,
    )
    if not result["drifted"]:
        return work_type
    suggested = result["suggested_work_type"]
    print(f"\nThis looks like it may have grown from `{work_type}` into `{suggested}`: {result['reasoning']}")
    choice = select_menu(
        "Reclassify, or keep it bounded to the original type?",
        [f"Reclassify as `{suggested}`", f"Keep it as `{work_type}`"],
    )
    return suggested if choice == 0 else work_type


def offer_local_architecture_note_interactive(
    registry: Path, repository_path: Path, objective: str, components: list[str],
) -> list[str]:
    """
    plan/active/planning-consistency-checks.md design section 2 -- checks
    whether the involved components' already-resolved architecture.profile_ids
    (resolved once, generically, at onboarding time) actually give real
    guidance for this specific objective, and if not, offers to draft a local,
    unreviewed architecture note (esc_exec.local_architecture) rather than
    silently proceeding with generic, not-really-relevant profile_ids.

    Warn-and-proceed, never a hard gate (explicit decision this session):
    returns [] (no notes) in every case where the check can't run or the human
    declines -- provider not connected, architecture framework route not
    resolvable, nothing resolved to check coverage against and the human
    declines anyway, or a covered result -- the plan proceeds exactly as today.
    Never drafts a note without the human explicitly confirming they want one.

    Returns repository-root-relative paths of any notes drafted this pass,
    ready to thread into generate_single_repository_workflow's
    local_architecture_notes parameter.
    """
    provider = active_provider(registry)
    if provider != {"id": "claude", "route": "subscription"}:
        return []
    try:
        framework_root = resolve_route(registry, "frameworks", ARCHITECTURE_FRAMEWORK_ID)
        architecture_index = load_architecture_index(framework_root)
    except (KeyError, FileNotFoundError, ValueError):
        return []

    index = load_repository_index(repository_path)
    doc_ids = architecture_doc_ids_for_components(repository_path, index, components)
    documents, _missing = resolve_architecture_docs(doc_ids, architecture_index)
    result = suggest_architecture_coverage_gap(ClaudeCodeClient(), framework_root, objective, documents)
    if result["covered"]:
        return []

    print(f"\nArchitecture framework coverage looks thin for this objective: {result['reasoning']}")
    if not confirm(f"Draft a local architecture note (\"{result['suggested_title']}\")? Unreviewed, local to this repository only."):
        return []
    try:
        body = ask("Briefly describe the guidance this note should capture:").strip()
    except (EOFError, KeyboardInterrupt):
        return []
    slug = re.sub(r"[^a-z0-9]+", "-", result["suggested_title"].lower()).strip("-") or "note"
    note_path = write_local_architecture_note(
        repository_path, slug,
        doc_id=f"LOCAL-{slug.upper()}", doc_type="pattern", layer="pattern",
        platform=["all"], architecture=["all"], title=result["suggested_title"],
        body=body or "(no detail captured yet -- expand this before promoting it.)",
    )
    return [str(note_path.relative_to(repository_path))]


def confirm_components_interactive(components: list[dict[str, str]]) -> set[str] | None:
    """
    Always-shown component confirmation step (plan/active/generic-multi-component-
    detection.md design section 4) -- shows every component about to be onboarded
    (Tier 1-detected and Tier 2 AI-resolved alike) and lets the user deselect any
    they don't want (test fixtures, deprecated modules, samples, ...) before any
    manifest is generated or purpose/frameworks question is asked. Applies
    uniformly regardless of adapter or whether AI was involved in resolving
    anything -- decided explicitly, not conditional on ambiguity having occurred.

    Simple v1: typed numbers, not a picker (see that plan's open question 1) --
    matches select_menu's own non-TTY fallback style and needs no new picker
    infrastructure.

    Returns the set of excluded component IDs (empty if none), or None if the
    user backed out (EOF/Ctrl-C) -- mirrors select_menu's own None-means-cancelled
    convention.
    """
    if not components:
        return set()
    print_question(f"\n{len(components)} component(s) found:")
    for index, component in enumerate(components, 1):
        print(f"  {index}. {component['id']} ({component['path']})")
    try:
        raw = ask("Exclude any? (comma-separated numbers, or Enter to include all)").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw:
        return set()
    excluded_indices: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(components):
            excluded_indices.add(int(token) - 1)
    return {components[index]["id"] for index in excluded_indices}


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
        # A real directory exists but no adapter detected a build system in it
        # ("empty-but-real" -- see plan/done/scaffold-new-or-empty-repository.md).
        print(render_wizard_suggestion(
            f"No supported build system detected under `{Path(raw).expanduser().resolve()}` ({exc}).",
            "Then come back to this menu and enter the path again.",
        ))
        return 1
    except (KeyError, FileNotFoundError):
        # Not an existing directory, and not a registered repository either --
        # "no location at all" collapses into the same answer as the case above.
        print(render_wizard_suggestion(
            f"Nothing found at `{raw}`.",
            "Then come back to this menu and enter the new project's path.",
        ))
        return 1

    # Tier 2 AI fallback for module identity -- generic across adapters, keyed off
    # BuildSystemAdapter.unresolved(), not Gradle-specific (see
    # plan/active/generic-multi-component-detection.md design section 3). Runs
    # before analyze() so its answer is reflected in this session's proposal
    # immediately, not just after being persisted by a later apply.
    _, _, adapter = detect_build_system(repository_path)
    unresolved = adapter.unresolved(repository_path)
    extra_resolved: dict[str, str] = {}
    resolution_session_id: str | None = None
    if unresolved:
        if active_provider(registry) == {"id": "claude", "route": "subscription"}:
            print(f"\n{len(unresolved)} declared module(s) could not be resolved to a real directory -- asking AI to help locate them...")
            resolution = suggest_unresolved_components(ClaudeCodeClient(), repository_path, unresolved)
            extra_resolved = resolution["resolved"]
            resolution_session_id = resolution["session_id"]
            if extra_resolved:
                print("Resolved:")
                for identifier, relative in extra_resolved.items():
                    print(f"  {identifier} -> {relative}")
            still_unresolved = [identifier for identifier in unresolved if identifier not in extra_resolved]
            if still_unresolved:
                print(f"Could not resolve, will be skipped: {', '.join(still_unresolved)}")
        else:
            print(
                f"\n{len(unresolved)} declared module(s) could not be resolved to a real directory: "
                f"{', '.join(unresolved)}. Connect an AI provider (Configure system) to help resolve "
                "them -- skipping for now."
            )

    existing_proposal = store.get_onboarding_proposal(repository_id)
    try:
        proposal = analyze(store, registry, repository_id, repository_path, extra_resolved or None)
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

    newly_excluded_ids = confirm_components_interactive(proposal["components"])
    if newly_excluded_ids is None:
        print("\nCancelled -- nothing was written. The proposal is saved; resume anytime by running escape-ai again.")
        return 0

    answers: dict[str, dict[str, Any]] = {}
    questions = [q for q in proposal["semantic_questions"] if q["component_id"] not in newly_excluded_ids]
    purpose_component_ids = [question["component_id"] for question in questions if question["field"] == "purpose"]
    frameworks_component_ids = [question["component_id"] for question in questions if question["field"] == "frameworks"]
    ai_answerable_count = len(set(purpose_component_ids) | set(frameworks_component_ids))
    if ai_answerable_count and active_provider(registry) is None:
        print(f"\n{ai_answerable_count} component(s) have questions an AI provider can suggest answers for -- confirm or edit, instead of typing from scratch.")
        prompt_provider_setup_interactive(registry)
    if ai_answerable_count and active_provider(registry) is not None:
        print("Thinking... (reading source to suggest answers -- this can take a minute)")
    suggestions = suggest_answers_via_provider(
        registry, repository_path, purpose_component_ids, frameworks_component_ids, resolution_session_id,
    )
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
        result = apply_answers(
            store, registry, repository_id, repository_path, answers,
            resolved_components=extra_resolved or None,
            excluded_component_ids=sorted(newly_excluded_ids) or None,
        )
    except (OSError, ValueError) as exc:
        print(f"Apply failed: {exc}")
        return 1
    print(render_apply_result(result))
    print(render_onboarding_map(repository_path))

    try:
        choice = ask(f"\nPress Enter to plan new work for `{repository_id}` now, or anything else to return to the main menu:").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if not choice:
        return run_planning_interactive(store, registry, prefilled_repository_id=repository_id)
    return 0


def run_planning_conversation_interactive(
    registry: Path, repository_path: Path, repository_id: str, initiative_id: str, objective: str,
) -> str | None:
    """
    Optional AI-assisted back-and-forth to think through scope before the static
    scope_boundary/completion_conditions/rollout_needs questions are asked --
    Consumer 1 of plan/done/ai-conversation-primitive.md, proven against planning
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
    if proposal:
        # Show current vs. proposed before asking -- resolves
        # ai-conversation-primitive.md's open question 7: this confirm used to ask
        # a yes/no question about an unseen change. Reuses render_roadmap, the same
        # rendering `roadmap show` and "Configure system" use, rather than a
        # separate format.
        prior_purpose = (existing_roadmap or {}).get("project_roadmap", {}).get("purpose", "")
        proposed_document = {"project_roadmap": {
            "purpose": proposal.get("purpose") or prior_purpose,
            "current_stage": proposal.get("current_stage") or "",
            "direction": proposal.get("direction") or "",
            "durable_decisions": proposal.get("durable_decisions") or [],
        }}
        print("Current roadmap:")
        print(render_roadmap(existing_roadmap))
        print("Proposed roadmap:")
        print(render_roadmap(proposed_document))
        if confirm("Update this repository's saved roadmap with what was just discussed?"):
            roadmap = proposed_document["project_roadmap"]
            save_project_roadmap(
                repository_path, repository_id,
                roadmap["purpose"], roadmap["current_stage"], roadmap["direction"], roadmap["durable_decisions"],
            )
            print("Roadmap updated.")
    return last_text


def run_form_driven_planning_conversation_interactive(registry: Path, repository_path: Path, objective: str) -> dict[str, Any] | None:
    """
    plan/active/form-driven-planning-conversation.md -- the "chat about it"
    work-type option, offered only for single-repository plans (see
    CHAT_ABOUT_IT_OPTION). Consumer 2 of plan/done/ai-conversation-primitive.md's
    run_turn.

    Provider-gated the same "no wall" way as run_planning_conversation_
    interactive -- returns None immediately, at zero cost, whenever no claude/
    subscription provider is connected; the caller falls back to the plain
    five-item work-type menu.

    Ends when every required field (work_type, objective, completion_conditions)
    is filled *and* the human confirms using it, when the human sends a blank
    line early (whatever the trailer captured so far is used, same "never trap
    someone in a mandatory Q&A" discipline as every other conversation in this
    system), or when the hard context threshold is hit (forced stop, same
    two-tier safety net run_planning_conversation_interactive already uses).

    Returns the accumulated form dict (only ever containing keys the model was
    genuinely confident about, per suggest_form_turn), or None if no
    conversation happened at all. The caller falls back to the exact existing
    plain question for anything missing from it.
    """
    provider = active_provider(registry)
    if provider != {"id": "claude", "route": "subscription"}:
        print("No AI provider connected -- pick a work type from the list instead.")
        return None

    suggested_components = [match.component_id for match in route_objective(repository_path, objective)]
    client = ClaudeCodeClient()
    session_id: str | None = None
    form: dict[str, Any] = {}
    message = f"I want to plan this: {objective}"

    print_question("Let's talk through this -- send a blank line anytime to stop and use whatever's been captured so far.")
    while True:
        turn = suggest_form_turn(client, repository_path, message, objective, suggested_components, resume_session_id=session_id)
        session_id = turn["session_id"] or session_id
        if turn["reply"]:
            print_question(turn["reply"])
        form.update(turn["form"])

        if turn["threshold"] == "hard":
            print("This conversation has used most of the model's context window -- wrapping up now.")
            break
        if turn["threshold"] == "soft":
            print("(This conversation is getting long -- consider wrapping up soon.)")

        if all(form.get(field) for field in ("work_type", "objective", "completion_conditions")):
            print(f"\nLooks like that's everything needed: work_type={form['work_type']}, objective={form['objective']}")
            if confirm("Use this and finish up?"):
                break

        try:
            message = ask("You (blank line to finish):").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not message:
            break

    return form or None


def run_planning_interactive(store: Store, registry: Path, prefilled_repository_id: str | None = None) -> int:
    """
    prefilled_repository_id skips the "Repositories:" question entirely --
    used when arriving here straight from a just-finished onboarding (see
    run_onboarding_interactive's post-apply prompt) so momentum isn't lost
    re-typing/re-selecting the repo you were just looking at.

    Repositories/objective/initiative ID are asked *before* the work-type menu
    (plan/active/form-driven-planning-conversation.md design section 1,
    reordered from repositories-last) so the menu can offer a 6th option --
    CHAT_ABOUT_IT_OPTION, single-repository plans only -- that talks through
    work_type (and, along the way, objective/components/scope_boundary/
    completion_conditions/rollout_needs) instead of picking blind. draft_plan
    itself never actually uses work_type for routing/question-generation (only
    validates and stores it), so this reordering needed no changes there.
    """
    try:
        if prefilled_repository_id:
            repository_values = [prefilled_repository_id]
        else:
            repos_raw = ask("Repositories (comma-separated IDs or paths):").strip()
            repository_values = [value.strip() for value in repos_raw.split(",") if value.strip()]
        objective = ask("Objective:").strip()
        initiative_id = ask("Initiative/task ID (a short slug, e.g. feature-user-export):").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written.")
        return 0

    work_type_options = list(WORK_TYPES)
    if len(repository_values) == 1:
        work_type_options.append(CHAT_ABOUT_IT_OPTION)
    choice = select_menu(render_work_types(), work_type_options)
    if choice is None:
        return 0

    form: dict[str, Any] | None = None
    if work_type_options[choice] == CHAT_ABOUT_IT_OPTION:
        try:
            _, repository_path_for_chat = resolve_repository(repository_values[0], registry)
        except (KeyError, FileNotFoundError) as exc:
            print(f"Could not resolve this repository: {exc}")
            return 1
        form = run_form_driven_planning_conversation_interactive(registry, repository_path_for_chat, objective)
        if form and form.get("work_type"):
            work_type = form["work_type"]
        else:
            fallback_choice = select_menu(render_work_types(), list(WORK_TYPES))
            if fallback_choice is None:
                return 0
            work_type = WORK_TYPES[fallback_choice]
        if form and form.get("objective"):
            objective = form["objective"]
    else:
        work_type = work_type_options[choice]

    try:
        draft = draft_plan(store, registry, initiative_id, work_type, objective, repository_values)
    except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
        print(f"Could not draft this plan: {exc}")
        return 1
    print(render_plan_draft(draft))

    used_form_conversation = bool(form)
    if len(draft["repositories"]) == 1 and not used_form_conversation:
        try:
            repository_id, repository_path = resolve_repository(draft["repositories"][0], registry)
            run_planning_conversation_interactive(registry, repository_path, repository_id, initiative_id, objective)
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled the conversation -- continuing with the plan questions.")

    answers: dict[str, Any] = {}
    if form:
        # Pre-seed from the conversation -- the loop below skips re-asking
        # anything already present here (plan/active/form-driven-planning-
        # conversation.md design section 3's fallback discipline).
        if form.get("components"):
            answers.setdefault("components", {})[draft["repositories"][0]] = form["components"]
        for field in ("scope_boundary", "completion_conditions", "rollout_needs"):
            if form.get(field):
                answers[field] = form[field]

    try:
        for question in draft["questions"]:
            if question["field"] == "components":
                repository_id = question["repository"]
                if repository_id in answers.get("components", {}):
                    continue
                value = ask(question["prompt"]).strip()
                answers.setdefault("components", {})[repository_id] = [item.strip() for item in value.split(",") if item.strip()]
            elif question["field"] == "depends_on":
                repository_id = question["repository"]
                if repository_id in answers.get("depends_on", {}):
                    continue
                value = ask(question["prompt"]).strip()
                answers.setdefault("depends_on", {})[repository_id] = [item.strip() for item in value.split(",") if item.strip()]
            elif question["field"] in answers:
                continue
            elif question["field"] == "completion_conditions":
                value = ask(question["prompt"]).strip()
                answers["completion_conditions"] = [item.strip() for item in value.split(",") if item.strip()]
            else:
                answers[question["field"]] = ask(question["prompt"]).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled -- nothing was written. The draft is saved; resume anytime by running escape-ai again.")
        return 0

    store.save_plan_pending_answers(initiative_id, answers)

    if not used_form_conversation:
        # Redundant to re-litigate immediately after a conversation that just
        # freshly settled work_type through real back-and-forth -- only runs
        # for the deterministic five-item-menu path, same as before.
        try:
            _, repository_path_for_check = resolve_repository(draft["repositories"][0], registry)
            confirmed_work_type = confirm_work_type_drift_interactive(
                registry, repository_path_for_check, work_type, objective,
                answers.get("scope_boundary", ""), answers.get("completion_conditions", []),
            )
        except (KeyError, FileNotFoundError):
            confirmed_work_type = work_type  # repo no longer resolvable -- skip the check, don't block planning over it
        if confirmed_work_type != work_type:
            store.save_plan_draft(initiative_id, confirmed_work_type, objective, draft["repositories"], draft["routing"], draft["questions"])

    local_architecture_notes_by_repo: dict[str, list[str]] = {}
    for repository_id in draft["repositories"]:
        try:
            _, repository_path_for_notes = resolve_repository(repository_id, registry)
        except (KeyError, FileNotFoundError):
            continue
        notes = offer_local_architecture_note_interactive(
            registry, repository_path_for_notes, objective, answers.get("components", {}).get(repository_id, []),
        )
        if notes:
            local_architecture_notes_by_repo[repository_id] = notes

    if not confirm("Generate workflow files from these answers?"):
        print("Cancelled -- nothing was written. The draft and answers are saved; resume anytime by running escape-ai again.")
        return 0

    try:
        result, dependency_chain = apply_plan(store, registry, initiative_id, answers, local_architecture_notes_by_repo)
    except (OSError, ValueError) as exc:
        print(f"Apply failed: {exc}")
        return 1
    print(render_plan_result(result, dependency_chain))
    return 0


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


def _resume_item_label(item: dict[str, Any]) -> str:
    status = item["latest_run_status"] or "never run"
    checkpoint = " [checkpoint pending]" if item["checkpoint_present"] else ""
    return f"{item['repository_id']}/{item['task_id']} -- {status}, {item['attempts']} attempt(s){checkpoint} -- {item['objective']}"


def run_observe_interactive(store: Store, registry: Path) -> int:
    """
    "Observe a run" -- a task picker (reusing `active_work`, same as "Resume active
    work") followed by a read-only drill-down over that task's latest recorded run
    (see `run_detail`'s docstring for why this is a post-hoc view, not a live tail).
    """
    items = active_work(store, registry)
    if not items:
        print(render_active_work(items))
        return 0
    choice = select_menu("Observe a run -- select a task:", [_resume_item_label(item) for item in items])
    if choice is None:
        return 0
    selected = items[choice]
    repository_id, task_id = selected["repository_id"], selected["task_id"]
    _, repository_path = resolve_repository(repository_id, registry)
    print(render_run_detail(repository_id, task_id, run_detail(store, task_id), repository_path))
    return 0


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

    action_choice = select_menu(
        f"{repository_id}/{task_id} -- choose an action:",
        ["Execute now", "Promote checkpoint candidate", "Observe latest run"],
    )
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
        # Cheap, no-dispatch pre-flight (see plan/done/pre-flight-doctor-and-gate-
        # prerequisites.md and plan/active/interactive-menu-completeness.md design
        # 5) -- the same check `task doctor`/`task run`'s automatic gate already
        # run, surfaced here too so the guided path doesn't burn a real dispatch
        # attempt on a gap this would have caught for free. Non-blocking, same as
        # `task run`: a blocker is shown, not enforced -- `--yes`/this confirm
        # remains the actual gate.
        blockers = doctor_check(repository_path, task_path, registry)
        if blockers:
            print(f"BLOCKED    {len(blockers)} pre-flight issue(s) found; a real dispatch would likely fail before this task even starts:")
            for blocker in blockers:
                print(f"  - {blocker}")
        print(render_execution_preview(
            repository_id, task_id, load_yaml(task_path), provider,
            resolve_default_policy(registry), prior_consent(store, task_id),
        ))
        if not confirm("Execute this task now?"):
            print("Cancelled -- nothing was executed.")
            return 0
        result = execute_task(store, registry, repository_id, repository_path, task_id, provider)
        print(render_execution_result(result))
        return 0

    if action_choice == 1:
        try:
            candidate = checkpoint_candidate(store, repository_path, task_id)
        except ValueError as exc:
            print(f"Cannot promote: {exc}")
            return 1
        print(render_checkpoint_candidate(candidate, repository_path))
        if not confirm("Promote this checkpoint into the durable workflow?"):
            print("Cancelled -- nothing was promoted.")
            return 0
        path = promote_checkpoint(repository_path, task_id, candidate)
        print(f"Merged worktree back; no checkpoint to record." if path is None else f"Promoted checkpoint to {path}")
        return 0

    if action_choice == 2:
        print(render_run_detail(repository_id, task_id, run_detail(store, task_id), repository_path))
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
    plan_draft.add_argument("--json", action="store_true")

    plan_answer = plan_commands.add_parser("answer")
    plan_answer.add_argument("initiative_id")
    plan_answer.add_argument("answers_file", type=Path)

    plan_commands.add_parser("apply").add_argument("initiative_id")
    plan_status = plan_commands.add_parser("status")
    plan_status.add_argument("initiative_id")
    plan_status.add_argument("--json", action="store_true")
    plan_commands.add_parser(
        "ready", help="List tasks in this initiative that are unblocked and never submitted",
    ).add_argument("initiative_id")

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

    task_impact = task_commands.add_parser("impact", help="Show which other initiative tasks this completed task unblocks")
    task_impact.add_argument("task_id")
    task_promote.add_argument("--yes", action="store_true", help="Actually promote; without this, preview only")

    task_doctor = task_commands.add_parser(
        "doctor", help="Check a task's architecture coverage and verification-gate prerequisites, without dispatching",
    )
    task_doctor.add_argument("repository")
    task_doctor.add_argument("task_id")

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

    policy = subcommands.add_parser("policy", help="Show or select the default policy profile")
    policy_commands = policy.add_subparsers(dest="policy_command", required=True)
    policy_commands.add_parser("show")
    policy_set = policy_commands.add_parser("set")
    policy_set.add_argument("profile_id", choices=list(POLICY_PROFILES))

    roadmap = subcommands.add_parser("roadmap", help="Show or set a repository's project roadmap")
    roadmap_commands = roadmap.add_subparsers(dest="roadmap_command", required=True)
    roadmap_commands.add_parser("show").add_argument("repository")
    roadmap_set = roadmap_commands.add_parser("set")
    roadmap_set.add_argument("repository")
    roadmap_set.add_argument("answers_file", type=Path)

    return parser


def _dispatch_repository(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.repository_command == "add":
        add_route(registry, "repositories", args.id, args.path)
        print(f"REGISTERED repository `{args.id}` -> {args.path.expanduser().resolve()}")
        return 0

    if args.repository_command == "analyze":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
        except ValueError as exc:
            print(render_wizard_suggestion(
                f"No supported build system detected under `{args.repository}` ({exc}).",
                "Then run: escape-ai repository add <id> <path> && escape-ai repository analyze <id>",
            ))
            return 1
        except (KeyError, FileNotFoundError):
            print(render_wizard_suggestion(
                f"`{args.repository}` isn't a directory and isn't a registered repository.",
                "Then run: escape-ai repository add <id> <path> && escape-ai repository analyze <id>",
            ))
            return 1
        try:
            proposal = analyze(store, registry, repository_id, repository_path)
        except (OSError, ValueError) as exc:
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
        print(json.dumps(draft, indent=2) if args.json else render_plan_draft(draft))
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
            result, dependency_chain = apply_plan(store, registry, args.initiative_id, pending["answers"])
        except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_plan_result(result, dependency_chain))
        return 0

    if args.plan_command == "status":
        draft = store.get_plan_draft(args.initiative_id)
        pending = store.get_plan_pending_answers(args.initiative_id)
        result = store.get_plan_result(args.initiative_id)
        info = {
            "initiative_id": args.initiative_id,
            "has_draft": draft is not None,
            "has_pending_answers": pending is not None,
            "has_result": result is not None,
            "process_metrics": planning_process_metrics(store, args.initiative_id),
        }
        # --json also surfaces the real pending-question array (see
        # plan/done/cli-discoverability.md finding #2) -- omitted from the plain
        # render, which stays a compact summary.
        print(json.dumps({**info, "questions": draft["questions"] if draft else []}, indent=2) if args.json else render_status(info))
        return 0

    if args.plan_command == "ready":
        ready = find_ready_tasks(store, registry, args.initiative_id)
        print(json.dumps(ready, indent=2))
        return 0

    return 1


def _dispatch_task(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.task_command == "run":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            task_path = repository_path / ".esc-ai" / "workflows" / "active" / args.task_id / "task.yaml"
            if not task_path.is_file():
                print(f"INVALID    no task.yaml found for `{args.task_id}` in `{repository_id}`")
                suggestions = _task_id_suggestions(repository_path, args.task_id)
                if suggestions:
                    print(f"           did you mean: {', '.join(suggestions)}?")
                return 1
            task_document = load_yaml(task_path)
        except (KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        provider = active_provider(registry)
        print(render_execution_preview(
            repository_id, args.task_id, task_document, provider,
            resolve_default_policy(registry), prior_consent(store, args.task_id),
        ))
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
            candidate = checkpoint_candidate(store, repository_path, args.task_id)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            print(f"INVALID    {exc}")
            return 1
        print(render_checkpoint_candidate(candidate, repository_path))
        if not args.yes:
            print("Preview only -- re-run with --yes to actually promote.")
            return 0
        path = promote_checkpoint(repository_path, args.task_id, candidate)
        print("Merged worktree back; no checkpoint to record." if path is None else f"Promoted checkpoint to {path}")
        return 0

    if args.task_command == "impact":
        try:
            document = analyze_task_impact(store, registry, args.task_id)
        except ValueError as exc:
            print(f"INVALID    {exc}")
            return 1
        print(json.dumps(document, indent=2))
        return 0

    if args.task_command == "doctor":
        try:
            repository_id, repository_path = resolve_repository(args.repository, registry)
            task_path = repository_path / ".esc-ai" / "workflows" / "active" / args.task_id / "task.yaml"
            if not task_path.is_file():
                print(f"INVALID    no task.yaml found for `{args.task_id}` in `{repository_id}`")
                suggestions = _task_id_suggestions(repository_path, args.task_id)
                if suggestions:
                    print(f"           did you mean: {', '.join(suggestions)}?")
                return 1
            blockers = doctor_check(repository_path, task_path, registry)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            print(f"INVALID    {exc}")
            return 1
        if not blockers:
            print("CLEAN      architecture coverage and verification-gate prerequisites are both satisfied.")
            return 0
        print(f"BLOCKED    {len(blockers)} issue(s) found; a real `task run` would fail before this task even starts:")
        for blocker in blockers:
            print(f"  - {blocker}")
        return 1

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


def _dispatch_policy(args: argparse.Namespace, store: Store, registry: Path) -> int:
    if args.policy_command == "show":
        print(render_policy_status(default_policy_id(registry)))
        return 0
    if args.policy_command == "set":
        set_default_policy(registry, args.profile_id)
        print(f"SET        default policy to `{args.profile_id}`.")
        return 0
    return 1


def _dispatch_roadmap(args: argparse.Namespace, store: Store, registry: Path) -> int:
    try:
        repository_id, repository_path = resolve_repository(args.repository, registry)
    except (KeyError, FileNotFoundError) as exc:
        print(f"INVALID    {exc}")
        return 1
    if args.roadmap_command == "show":
        print(render_roadmap(load_project_roadmap(repository_path)))
        return 0
    if args.roadmap_command == "set":
        # A field omitted from answers_file keeps its current saved value rather
        # than being blanked -- a roadmap update is normally a small delta, not a
        # full restatement (see plan/done/project-vision-and-direction.md open
        # question 1).
        answers = json.loads(args.answers_file.read_text(encoding="utf-8"))
        current = (load_project_roadmap(repository_path) or {}).get("project_roadmap", {})
        save_project_roadmap(
            repository_path, repository_id,
            answers.get("purpose", current.get("purpose", "")),
            answers.get("current_stage", current.get("current_stage", "")),
            answers.get("direction", current.get("direction", "")),
            answers.get("durable_decisions", current.get("durable_decisions", [])),
        )
        print(f"SET        roadmap for `{repository_id}`.")
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
    if args.command == "policy":
        return _dispatch_policy(args, store, registry)
    if args.command == "roadmap":
        return _dispatch_roadmap(args, store, registry)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
