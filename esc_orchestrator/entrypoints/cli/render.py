"""Pure text renderers for the escape-ai CLI: values in, strings out, no I/O (PYEP-RENDER-01).

Over 400 lines (PYMOD-SIZE-01) by design: this module holds one concept -- turning already-computed
values into human-readable text -- as 25 small, independent functions. Splitting it would scatter
that one concept without reducing coupling. Enforced pure by the `pure-renderers` import contract.
"""
from __future__ import annotations

import json
from typing import Any

from esc_exec.claude_policy import granted_categories
from esc_exec.model import ManifestState, ValidationResult
from esc_exec.procedures import PROCEDURES
from esc_orchestrator.domain.intents import INTENT_SUMMARIES, INTENT_WORK_TYPES
from esc_orchestrator.domain.policy_profiles import (
    DEFAULT_POLICY_PROFILE_ID,
    POLICY_PROFILES,
)
from esc_orchestrator.domain.repositories import RepositoryLocation

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


def render_onboarding_map(repository_map: dict[str, Any]) -> str:
    """
    What onboarding actually produced -- "the map": what escape-ai now understands this
    repository to be, the same information Plan new work's routing and task execution
    actually read. Pure (PYEP-RENDER-01): `application.repositories.repository_map` reads the
    generated manifests from disk, so a user who hand-edits a manifest always sees their real,
    current state, not a stale snapshot from the apply call that just ran.
    """
    lines = [f"\nRepository map for `{repository_map['id']}` ({repository_map['type']}):"]
    for component in repository_map["components"]:
        lines.append(f"\n  {component['id']}  ({component['path']}, {component['build_system']})")
        lines.append(f"    purpose: {component['purpose']}")
        profile_ids = component["profile_ids"]
        lines.append(f"    architecture profiles: {', '.join(profile_ids) if profile_ids else '(none resolved)'}")
        lines.append(f"    verification gates: {'declared' if component['has_verification'] else 'not built for this build system yet'}")
    excluded = repository_map["excluded"]
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


def render_repository_list(locations: list[RepositoryLocation]) -> str:
    if not locations:
        return "No repositories registered yet."
    lines = ["Registered repositories:"]
    for location in locations:
        if location.error is None:
            lines.append(f"  {location.id} -> {location.path}")
        else:
            lines.append(f"  {location.id} -> UNRESOLVABLE ({location.error})")
    return "\n".join(lines)


def render_work_types() -> str:
    return "Work type:"


_STAGE_KIND_LABELS = {"gate": "GATE", "question": "ask", "action": "run"}


def render_procedure(verb: str) -> str:
    """The stages a verb enforces, in order -- what its help and drafts show so the
    gates behind a friendly verb are never hidden (VISION.md, "what this is not")."""
    lines = [f"Procedure for `{verb}` (fixed for everyone; never skipped):"]
    for number, stage in enumerate(PROCEDURES[verb], 1):
        interaction = "asks you unless your preferences auto-resolve it" if stage.interaction == "variable" else "always the same"
        # A stage whose maps_to is "new" has no implementation behind it yet; say so
        # rather than let help imply a gate that isn't running.
        pending = "  (not yet enforced)" if stage.maps_to.startswith("new") else ""
        lines.append(f"  {number}. {stage.name:<17} [{_STAGE_KIND_LABELS[stage.kind]}] {interaction}{pending}")
    return "\n".join(lines)


def render_intent_overview() -> str:
    lines = ["Intent workflows (each runs a fixed procedure; see `escape-ai <verb> --help`):"]
    lines += [f"  {verb:<12} {INTENT_SUMMARIES[verb]}" for verb in INTENT_WORK_TYPES]
    return "\n".join(lines)


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


def render_checkpoint_candidate(candidate: dict[str, Any], worktree_diff: str = "") -> str:
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
    if worktree_diff:
        lines += ["Worktree diff:", *(f"  {line}" for line in worktree_diff.splitlines())]
    return "\n".join(lines)


def render_run_detail(
    repository_id: str, task_id: str, detail: dict[str, Any], worktree_diff: str = "",
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
        lines += ["", render_checkpoint_candidate(detail["checkpoint"], worktree_diff)]

    return "\n".join(lines)

