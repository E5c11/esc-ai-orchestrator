from __future__ import annotations

from esc_exec.procedures import PROCEDURES


# BLA-42: the public intent verbs. Each is a front door onto
# esc_exec.procedures.PROCEDURES[verb]; the value is the esc_exec.planning.WORK_TYPES
# entry the existing draft_plan/apply_plan/execute_task machinery knows it by, or None
# where no such work type exists yet (`plan`, `document` -- their pipelines land in
# BLA-44). The verb never decides which stages run; PROCEDURES does.
INTENT_WORK_TYPES: dict[str, str | None] = {
    "fix": "fix",
    "feature": "feature",
    "refactor": "refactor",
    "job": "maintenance",
    "investigate": "investigation",
    "plan": None,
    "document": None,
}


assert set(INTENT_WORK_TYPES) == set(PROCEDURES), "INTENT_WORK_TYPES must cover exactly esc_exec.procedures.PROCEDURES"


INTENT_SUMMARIES: dict[str, str] = {
    "fix": "Fix a bug: capture the root cause first, then change and verify.",
    "feature": "Add new behavior, gated on architecture coverage and verification.",
    "refactor": "Restructure without changing behavior; a baseline is captured to verify against.",
    "job": "Any other change (chores, upgrades); loosely stated objective, same gates as `feature`.",
    "investigate": "Read-only: find out how something works or why it happens. Never edits.",
    "plan": "Read-only: produce a plan document without changing code. (Not yet available -- BLA-44.)",
    "document": "Write documentation grounded in the repository's own index. (Not yet available -- BLA-44.)",
}


# The legacy `plan draft|answer|apply|status|ready` group moved to `initiative` so
# `plan` could become an intent verb; these first-arguments are still accepted under
# `plan` as a deprecated alias (see _rewrite_legacy_plan_argv).
LEGACY_PLAN_SUBCOMMANDS = ("draft", "answer", "apply", "status", "ready")


def intent_for_work_type(work_type: str) -> str | None:
    """The intent verb whose draft_plan work type is `work_type`, if any."""
    for verb, mapped in INTENT_WORK_TYPES.items():
        if mapped == work_type:
            return verb
    return None

