from __future__ import annotations

# Hardcoded and expected to grow organically as new stacks come up in practice
# (see plan/done/scaffold-new-or-empty-repository.md open question 4) -- not
# registry-editable, since the smallest reasonable start is enough here.
# Scaffolding a brand-new project from nothing is already a solved,
# deterministic problem each of these tools owns better than an AI conversation
# would; escape-ai's job stops at pointing to the right one, never generating
# the files itself (see that plan's "Non-goals").
WIZARD_COMMANDS: dict[str, str] = {
    "next.js": "npx create-next-app@latest",
    "react (vite)": "npx create-vite@latest -- --template react-ts",
    "node (plain)": "npm init -y",
    "spring boot": "https://start.spring.io (Spring Initializr), or the `spring init` CLI",
    "kotlin multiplatform": "https://kmp.jetbrains.com/ (Kotlin Multiplatform Wizard)",
    "rust": "cargo new <name>",
}


def render_wizard_suggestion(reason: str, next_step: str) -> str:
    """
    reason explains why escape-ai stopped (empty directory, no build system
    detected, nothing at that path yet, ...); next_step is what to do once the
    wizard has run. The instruction itself is otherwise fixed: run the matching
    external wizard yourself, then come back -- no conversation, no proposal
    engine, no new artifact (see plan/done/scaffold-new-or-empty-repository.md).
    """
    lines = [
        reason,
        "escape-ai doesn't scaffold new projects itself -- that's already a solved, "
        "deterministic problem each ecosystem's own tooling owns. Run the wizard for "
        "your stack, then come back:",
        "",
    ]
    width = max(len(stack) for stack in WIZARD_COMMANDS)
    lines += [f"  {stack.ljust(width)}   {command}" for stack, command in sorted(WIZARD_COMMANDS.items())]
    lines += ["", "Stack not listed? Any external scaffolding tool works the same way.", "", next_step]
    return "\n".join(lines)
