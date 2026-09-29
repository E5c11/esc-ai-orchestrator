from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.adapters import detect_build_system
from esc_exec.ai_suggestions import suggest_onboarding_answers
from esc_exec.claude_client import ClaudeCodeClient, ClaudeCodeError
from esc_exec.conversation import (
    suggest_groundable_answers_turn,
    suggest_unresolved_components,
)
from esc_exec.registry import active_provider
from esc_orchestrator.application.repositories import (
    analyze,
    apply_answers,
    repository_map,
    resolve_repository,
)
from esc_orchestrator.domain.errors import AppError, NotFoundError, UnsupportedRepositoryError
from esc_orchestrator.entrypoints.cli.interactive.configure import (
    prompt_provider_setup_interactive,
)
from esc_orchestrator.entrypoints.cli.interactive.planning import (
    run_planning_interactive,
)
from esc_orchestrator.entrypoints.cli.render import (
    render_apply_result,
    render_onboarding_map,
    render_proposal,
)
from esc_orchestrator.entrypoints.cli.terminal import (
    ask,
    confirm,
    print_question,
    select_menu,
)
from esc_orchestrator.scaffold_wizards import render_wizard_suggestion
from esc_orchestrator.store import Store


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
    except AppError:
        warning = " -- path no longer resolvable"
    proposal_record = store.get_onboarding_proposal(repository_id)
    pending = len(proposal_record["proposal"].get("semantic_questions", [])) if proposal_record else 0
    return f"{repository_id} ({pending} question(s) pending){warning}"


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
    except UnsupportedRepositoryError as exc:
        # A real directory exists but no adapter detected a build system in it
        # ("empty-but-real" -- see plan/done/scaffold-new-or-empty-repository.md).
        print(render_wizard_suggestion(
            f"No supported build system detected under `{Path(raw).expanduser().resolve()}` ({exc}).",
            "Then come back to this menu and enter the path again.",
        ))
        return 1
    except NotFoundError:
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
    except AppError as exc:
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
    except AppError as exc:
        print(f"Apply failed: {exc}")
        return 1
    print(render_apply_result(result))
    print(render_onboarding_map(repository_map(repository_path)))

    try:
        choice = ask(f"\nPress Enter to plan new work for `{repository_id}` now, or anything else to return to the main menu:").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if not choice:
        return run_planning_interactive(store, registry, prefilled_repository_id=repository_id)
    return 0

