from __future__ import annotations

import shutil
import sys

from esc_orchestrator.entrypoints.cli.render import render_menu_options

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

