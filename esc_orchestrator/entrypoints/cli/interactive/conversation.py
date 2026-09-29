from __future__ import annotations

from pathlib import Path
from typing import Any
from esc_exec.claude_code_adapter import ClaudeCodeClient
from esc_exec.conversation import compact_conversation, run_turn, suggest_form_turn
from esc_exec.planning import route_objective
from esc_exec.registry import active_provider
from esc_exec.roadmap import load_project_roadmap, save_project_roadmap
from esc_orchestrator.entrypoints.cli.render import render_roadmap
from esc_orchestrator.entrypoints.cli.render import render_roadmap

from esc_orchestrator.entrypoints.cli.render import render_roadmap
from esc_orchestrator.entrypoints.cli.terminal import ask, confirm, print_question


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

