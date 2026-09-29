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
from esc_exec.procedures import PROCEDURES
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
from esc_orchestrator.domain.intents import (
    INTENT_SUMMARIES,
    INTENT_WORK_TYPES,
    LEGACY_PLAN_SUBCOMMANDS,
    intent_for_work_type,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.domain.policy_profiles import (
    DEFAULT_POLICY_PROFILE_ID,
    POLICY_PROFILES,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.render import (
    BANNER,
    CHAT_ABOUT_IT_OPTION,
    MENU,
    _STAGE_KIND_LABELS,
    render_active_work,
    render_apply_result,
    render_checkpoint_candidate,
    render_execution_preview,
    render_execution_result,
    render_intent_overview,
    render_menu,
    render_menu_options,
    render_onboarding_map,
    render_plan_draft,
    render_plan_result,
    render_policy_status,
    render_procedure,
    render_proposal,
    render_provider_status,
    render_repository_list,
    render_roadmap,
    render_run_detail,
    render_status,
    render_system_validation,
    render_validation,
    render_work_types,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.application.planning import (
    apply_plan,
    draft_plan,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.application.providers import (
    DEFAULT_OPENCODE_SERVER,
    SUBSCRIPTION_CLI_INFO,
    _subscription_auth_status,
    _subscription_cli_available,
    connect_provider,
    default_adapter,
    default_workspace,
    resolve_default_policy,
    resolve_runtime,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.application.repositories import (
    analyze,
    apply_answers,
    onboarding_process_metrics,
    planning_process_metrics,
    registered_repository_ids,
    repository_status,
    resolve_repository,
    validate_all,
    validate_system,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.application.runs import (
    _task_id_suggestions,
    active_work,
    checkpoint_candidate,
    execute_task,
    prior_consent,
    promote_checkpoint,
    run_detail,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.domain.intents import (
    INTENT_SUMMARIES,
    INTENT_WORK_TYPES,
    LEGACY_PLAN_SUBCOMMANDS,
    intent_for_work_type,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.domain.policy_profiles import (
    DEFAULT_POLICY_PROFILE_ID,
    POLICY_PROFILES,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.render import (
    BANNER,
    CHAT_ABOUT_IT_OPTION,
    MENU,
    _STAGE_KIND_LABELS,
    render_active_work,
    render_apply_result,
    render_checkpoint_candidate,
    render_execution_preview,
    render_execution_result,
    render_intent_overview,
    render_menu,
    render_menu_options,
    render_onboarding_map,
    render_plan_draft,
    render_plan_result,
    render_policy_status,
    render_procedure,
    render_proposal,
    render_provider_status,
    render_repository_list,
    render_roadmap,
    render_run_detail,
    render_status,
    render_system_validation,
    render_validation,
    render_work_types,
)  # noqa: F401 -- compatibility re-export


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
    parser = argparse.ArgumentParser(
        prog="escape-ai", epilog=render_intent_overview(), formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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

    for verb, summary in INTENT_SUMMARIES.items():
        intent = subcommands.add_parser(
            verb, help=summary, description=summary,
            epilog=render_procedure(verb), formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        intent.add_argument("objective", help="What you want done, in a sentence")
        intent.add_argument(
            "-r", "--repository", action="append", dest="repositories", metavar="REPOSITORY",
            help="Repository ID or path; repeat for a multi-repository change (required)",
        )
        intent.add_argument("--initiative-id", help="Initiative slug (default: derived from the verb and objective)")
        intent.add_argument("--json", action="store_true")

    plan = subcommands.add_parser(
        "initiative", help="Draft, answer and apply a plan step by step (what the intent verbs drive)",
    )
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


def _initiative_id_for(verb: str, objective: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", objective.lower()).strip("-")[:40].strip("-")
    return f"{verb}-{slug}" if slug else verb


def _dispatch_intent(args: argparse.Namespace, store: Store, registry: Path) -> int:
    """BLA-42 front door: an intent verb drafts an initiative through the same
    draft_plan the `initiative` group and the interactive menu use. It only selects
    the procedure; every gate lives in PROCEDURES and the execution machinery."""
    verb = args.command
    work_type = INTENT_WORK_TYPES[verb]
    if work_type is None:
        print(f"UNAVAILABLE `{verb}` has no pipeline yet (planned in BLA-44). Its procedure will be:")
        print(render_procedure(verb))
        return 2
    if not args.repositories:
        print(f"INVALID    `{verb}` needs at least one repository: -r <repository-id-or-path>")
        return 1
    initiative_id = args.initiative_id or _initiative_id_for(verb, args.objective)
    if store.get_plan_draft(initiative_id) is not None:
        print(f"INVALID    initiative `{initiative_id}` already exists; pass --initiative-id to use another name.")
        return 1
    try:
        draft = draft_plan(store, registry, initiative_id, work_type, args.objective, args.repositories)
    except (OSError, ValueError, KeyError, FileNotFoundError) as exc:
        print(f"INVALID    {exc}")
        return 1
    if args.json:
        print(json.dumps({**draft, "intent": verb, "procedure": [stage.name for stage in PROCEDURES[verb]]}, indent=2))
        return 0
    print(render_plan_draft(draft))
    print()
    print(render_procedure(verb))
    print()
    print(f"Next: `escape-ai initiative answer {initiative_id} <answers.json>`, then `escape-ai initiative apply {initiative_id}`.")
    return 0


def _rewrite_legacy_plan_argv(argv: list[str]) -> list[str]:
    """`plan draft|answer|apply|status|ready ...` -> `initiative ...`, with a notice,
    so existing scripts survive `plan` becoming an intent verb."""
    index = 0
    while index < len(argv) and argv[index].startswith("-"):
        index += 2 if argv[index] in ("--registry", "--db") else 1
    if index + 1 < len(argv) and argv[index] == "plan" and argv[index + 1] in LEGACY_PLAN_SUBCOMMANDS:
        print(
            f"note: `escape-ai plan {argv[index + 1]}` is deprecated; use `escape-ai initiative {argv[index + 1]}`.",
            file=sys.stderr,
        )
        return [*argv[:index], "initiative", *argv[index + 1:]]
    return argv


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
        print(f"STORED     answers for `{args.initiative_id}`; run `escape-ai initiative apply {args.initiative_id}` to write them.")
        return 0

    if args.plan_command == "apply":
        pending = store.get_plan_pending_answers(args.initiative_id)
        if pending is None:
            print(f"INCOMPLETE no pending answers for `{args.initiative_id}`; run `escape-ai initiative answer` first.")
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
    args = build_parser().parse_args(_rewrite_legacy_plan_argv(list(sys.argv[1:] if argv is None else argv)))
    registry = args.registry or default_registry_path()
    store = Store(args.db)
    if args.command is None:
        return run_interactive(store, registry)
    if args.command == "repository":
        return _dispatch_repository(args, store, registry)
    if args.command in INTENT_WORK_TYPES:
        return _dispatch_intent(args, store, registry)
    if args.command == "initiative":
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

