"""Single-keypress confirmation prompts (no Enter required), cross-platform."""

from __future__ import annotations

import sys

from rich.markup import escape

from toolboxcli._common.console import console, die, warn

# Control characters a raw-mode read returns instead of the terminal acting on them.
_CTRL_C = "\x03"
_EOF_KEYS = {"\x04", "\x1a"}  # Ctrl+D (POSIX), Ctrl+Z (Windows)


def _read_key() -> str:
    """Read a single character from the controlling terminal without requiring Enter."""
    if sys.platform == "win32":
        import msvcrt

        ch = msvcrt.getch()
        return ch.decode(errors="ignore")

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch


def _no_answer(prompt: str, choices: str, default: str | None) -> str:
    """The reply when nobody can answer (stdin closed, as under cron or nohup, or Ctrl+D).

    Every caller asks permission for something optional or destructive, with "n" meaning
    "leave it", so "n" is the safe reading of silence — even where Enter would have meant
    yes. Without an "n" to fall back on there's no safe guess, so stop instead.
    """
    if "n" in choices:
        warn(f"No answer to “{escape(prompt)}” (input closed) — taking that as no")
        return "n"
    if default:
        return default
    die(f"No answer to “{prompt}” (input closed)")


def confirm(prompt: str, choices: str = "yn", default: str | None = None) -> str:
    """Prompt with a single keypress restricted to ``choices`` (case-insensitive).

    Returns the lowercase matched character. Falls back to line-buffered ``input()``
    when stdin isn't an interactive terminal (e.g. redirected/piped input). Ctrl+C
    raises KeyboardInterrupt as it would anywhere else; closed input or Ctrl+D is
    answered "n" (see _no_answer).
    """
    choices = choices.lower()
    hint = "/".join(choices)
    if default:
        hint = hint.replace(default, default.upper())

    if not sys.stdin.isatty():
        while True:
            try:
                reply = input(f"{prompt} [{hint}] ").strip().lower()
            except EOFError:
                print()
                return _no_answer(prompt, choices, default)
            if not reply and default:
                return default
            if reply and reply[0] in choices:
                return reply[0]

    # Both the prompt and the hint are escaped: "[y/N]" — and any "[1080p]" in a filename
    # the prompt quotes — would otherwise be read as rich markup and silently vanish.
    while True:
        console.print(f"[yellow][?][/yellow] {escape(prompt)} {escape(f'[{hint}]')} ", end="")
        try:
            reply = _read_key().lower()
        except EOFError:
            console.print()
            return _no_answer(prompt, choices, default)
        except Exception:
            try:
                reply = input().strip().lower()
            except EOFError:
                return _no_answer(prompt, choices, default)
        if reply == _CTRL_C:
            # Raw mode turns off the terminal's own Ctrl+C handling; restore its meaning.
            console.print()
            raise KeyboardInterrupt
        # read(1) returns "" at end of input rather than raising — and "" is "in" every
        # choices string, so it must be caught before the membership test below.
        if reply in _EOF_KEYS or reply == "":
            console.print()
            return _no_answer(prompt, choices, default)
        console.print(escape(reply))
        if reply == "\r" or reply == "\n":
            if default:
                return default
            continue
        if reply in choices:
            return reply
