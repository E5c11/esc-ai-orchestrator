from __future__ import annotations

from esc_orchestrator.application.app import App
from esc_orchestrator.application.repositories import validate_system
from esc_orchestrator.entrypoints.cli.interactive.configure import (
    run_configure_interactive,
)
from esc_orchestrator.entrypoints.cli.interactive.onboarding import (
    run_onboarding_interactive,
)
from esc_orchestrator.entrypoints.cli.interactive.planning import (
    run_planning_interactive,
)
from esc_orchestrator.entrypoints.cli.interactive.resume import (
    run_observe_interactive,
    run_resume_interactive,
)
from esc_orchestrator.entrypoints.cli.render import (
    MENU,
    render_menu,
    render_system_validation,
)
from esc_orchestrator.entrypoints.cli.terminal import select_menu


def run_interactive(app: App) -> int:
    """
    Loops back to this same menu after every action -- including one that ends
    in an error message (a bad repository path, a failed apply, ...) -- instead
    of exiting the whole process. A single action's own return code was
    previously propagated straight out of main(), so completing (or even just
    failing) one onboarding silently ended the entire session; the only
    deliberate exit is backing out of the menu itself (Esc/blank/EOF/Ctrl-C,
    handled by select_menu returning None).
    """
    registry = app.registry
    while True:
        choice = select_menu(render_menu(), MENU)
        if choice is None:
            return 0
        if choice == 0:
            run_onboarding_interactive(app)
        elif choice == 1:
            run_planning_interactive(app)
        elif choice == 2:
            run_resume_interactive(app)
        elif choice == 3:
            run_observe_interactive(app)
        elif choice == 4:
            run_configure_interactive(registry)
        elif choice == 5:
            print(render_system_validation(validate_system(registry)))

