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
from esc_orchestrator.entrypoints.cli.interactive.configure import (
    _pick_and_connect_provider_interactive,
    configure_policy_interactive,
    configure_provider_interactive,
    configure_roadmap_interactive,
    prompt_provider_setup_interactive,
    run_configure_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.conversation import (
    run_form_driven_planning_conversation_interactive,
    run_planning_conversation_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.menu import (
    run_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.onboarding import (
    _ask_with_optional_suggestion,
    _collect_answer,
    _parse_frameworks_pairs,
    _unfinished_onboarding_label,
    confirm_components_interactive,
    run_onboarding_interactive,
    suggest_answers_via_provider,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.planning import (
    confirm_work_type_drift_interactive,
    offer_local_architecture_note_interactive,
    run_planning_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.resume import (
    _resume_item_label,
    run_observe_interactive,
    run_resume_interactive,
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
from esc_orchestrator.entrypoints.cli.terminal import (
    _ANSWER_COLOR,
    _QUESTION_COLOR,
    _RESET_COLOR,
    _isatty,
    _select_menu_inline,
    _shown_menu_hint,
    ask,
    confirm,
    print_question,
    select_menu,
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
from esc_orchestrator.entrypoints.cli.dispatch import (
    _dispatch_intent,
    _dispatch_plan,
    _dispatch_policy,
    _dispatch_provider,
    _dispatch_repository,
    _dispatch_resume,
    _dispatch_roadmap,
    _dispatch_task,
    _initiative_id_for,
    _rewrite_legacy_plan_argv,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.configure import (
    _pick_and_connect_provider_interactive,
    configure_policy_interactive,
    configure_provider_interactive,
    configure_roadmap_interactive,
    prompt_provider_setup_interactive,
    run_configure_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.conversation import (
    run_form_driven_planning_conversation_interactive,
    run_planning_conversation_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.menu import (
    run_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.onboarding import (
    _ask_with_optional_suggestion,
    _collect_answer,
    _parse_frameworks_pairs,
    _unfinished_onboarding_label,
    confirm_components_interactive,
    run_onboarding_interactive,
    suggest_answers_via_provider,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.planning import (
    confirm_work_type_drift_interactive,
    offer_local_architecture_note_interactive,
    run_planning_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.interactive.resume import (
    _resume_item_label,
    run_observe_interactive,
    run_resume_interactive,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.main import (
    main,
)  # noqa: F401 -- compatibility re-export
from esc_orchestrator.entrypoints.cli.parser import (
    build_parser,
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
from esc_orchestrator.entrypoints.cli.terminal import (
    _ANSWER_COLOR,
    _QUESTION_COLOR,
    _RESET_COLOR,
    _isatty,
    _select_menu_inline,
    _shown_menu_hint,
    ask,
    confirm,
    print_question,
    select_menu,
)  # noqa: F401 -- compatibility re-export


if __name__ == "__main__":
    raise SystemExit(main())

