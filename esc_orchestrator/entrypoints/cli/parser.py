from __future__ import annotations

import argparse
from pathlib import Path

from esc_exec.registry import KNOWN_PROVIDERS
from esc_orchestrator.application.providers import DEFAULT_OPENCODE_SERVER
from esc_orchestrator.domain.intents import INTENT_SUMMARIES
from esc_orchestrator.domain.policy_profiles import POLICY_PROFILES
from esc_orchestrator.entrypoints.cli.render import (
    render_intent_overview,
    render_procedure,
)

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

