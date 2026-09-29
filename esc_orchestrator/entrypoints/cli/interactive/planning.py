from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from esc_exec.architecture_lookup import (
    load_architecture_index,
    resolve_architecture_docs,
)
from esc_exec.claude_code_adapter import (
    ClaudeCodeClient,
    suggest_architecture_coverage_gap,
    suggest_work_type_drift,
)
from esc_exec.local_architecture import write_local_architecture_note
from esc_exec.onboarding import ARCHITECTURE_FRAMEWORK_ID
from esc_exec.planning import (
    WORK_TYPES,
    architecture_doc_ids_for_components,
    load_repository_index,
)
from esc_exec.registry import active_provider, resolve_route
from esc_orchestrator.application.planning import apply_plan, draft_plan
from esc_orchestrator.application.repositories import resolve_repository
from esc_orchestrator.domain.intents import intent_for_work_type
from esc_orchestrator.entrypoints.cli.interactive.conversation import (
    run_form_driven_planning_conversation_interactive,
    run_planning_conversation_interactive,
)
from esc_orchestrator.entrypoints.cli.render import (
    CHAT_ABOUT_IT_OPTION,
    render_plan_draft,
    render_plan_result,
    render_procedure,
    render_work_types,
)
from esc_orchestrator.entrypoints.cli.terminal import ask, confirm, select_menu
from esc_orchestrator.store import Store


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

    verb = intent_for_work_type(work_type)
    if verb is not None:
        print(render_procedure(verb))

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

