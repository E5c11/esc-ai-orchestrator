"""Compatibility facade for the escape-ai CLI (BLA-42 / ORCH-PY-MODULARISE).

The implementation lives in `esc_orchestrator.entrypoints.cli` (surface), `.application`
(operations) and `.domain` (vocabulary). This module keeps `esc_orchestrator.escape_ai_cli`
importable -- the `escape-ai` console script, `python -m`, and the names existing callers
and tests reach for -- without holding any logic of its own.
"""
from __future__ import annotations

from esc_exec.claude_client import ClaudeCodeError  # noqa: F401
from esc_exec.claude_policy import granted_categories  # noqa: F401
from esc_exec.planning import WORK_TYPES  # noqa: F401
from esc_exec.registry import (  # noqa: F401
    active_provider,
    add_route,
    default_policy_id,
    set_default_policy,
    set_provider,
)
from esc_exec.yaml_io import load_yaml  # noqa: F401
from esc_orchestrator.composition import build_app, resolve_runtime  # noqa: F401
from esc_orchestrator.application.providers import (  # noqa: F401
    connect_provider,
    default_adapter,
    default_workspace,
    resolve_default_policy,
)
from esc_orchestrator.application.repositories import (  # noqa: F401
    registered_repository_ids,
    repository_locations,
    validate_system,
)
from esc_orchestrator.application.runs import (  # noqa: F401
    _task_id_suggestions,
    active_work,
    checkpoint_candidate,
    execute_task,
    prior_consent,
    promote_checkpoint,
    run_detail,
    worktree_diff,
)
from esc_orchestrator.domain.intents import INTENT_WORK_TYPES  # noqa: F401
from esc_orchestrator.domain.policy_profiles import (  # noqa: F401
    DEFAULT_POLICY_PROFILE_ID,
    POLICY_PROFILES,
)
from esc_orchestrator.entrypoints.cli.dispatch_workflow import (
    _rewrite_legacy_plan_argv,  # noqa: F401
)
from esc_orchestrator.entrypoints.cli.interactive.configure import (  # noqa: F401
    configure_policy_interactive,
    configure_roadmap_interactive,
    prompt_provider_setup_interactive,
    run_configure_interactive,
)
from esc_orchestrator.entrypoints.cli.interactive.conversation import (  # noqa: F401
    run_planning_conversation_interactive,
)
from esc_orchestrator.entrypoints.cli.interactive.menu import (
    run_interactive,  # noqa: F401
)
from esc_orchestrator.entrypoints.cli.interactive.onboarding import (  # noqa: F401
    _collect_answer,
    run_onboarding_interactive,
)
from esc_orchestrator.entrypoints.cli.interactive.planning import (  # noqa: F401
    confirm_work_type_drift_interactive,
    run_planning_interactive,
)
from esc_orchestrator.entrypoints.cli.interactive.resume import (  # noqa: F401
    run_observe_interactive,
    run_resume_interactive,
)
from esc_orchestrator.entrypoints.cli.main import run
from esc_orchestrator.entrypoints.cli.parser import build_parser  # noqa: F401
from esc_orchestrator.entrypoints.cli.render import (  # noqa: F401
    CHAT_ABOUT_IT_OPTION,
    MENU,
    render_active_work,
    render_apply_result,
    render_checkpoint_candidate,
    render_execution_preview,
    render_execution_result,
    render_menu,
    render_menu_options,
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
)
from esc_orchestrator.entrypoints.cli.terminal import _isatty, select_menu  # noqa: F401


def main(argv: list[str] | None = None) -> int:
    """The `escape-ai` console-script entry point: run the CLI with the real composition root."""
    return run(argv, build_app)


if __name__ == "__main__":
    raise SystemExit(main())
