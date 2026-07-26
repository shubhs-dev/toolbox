"""Single-keypress confirmation prompts (no Enter required), cross-platform."""

from __future__ import annotations

import sys

from toolboxcli._common.console import console


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


def confirm(prompt: str, choices: str = "yn", default: str | None = None) -> str:
    """Prompt with a single keypress restricted to ``choices`` (case-insensitive).

    Returns the lowercase matched character. Falls back to line-buffered ``input()``
    when stdin isn't an interactive terminal (e.g. redirected/piped input).
    """
    choices = choices.lower()
    hint = "/".join(choices)
    if default:
        hint = hint.replace(default, default.upper())

    if not sys.stdin.isatty():
        while True:
            reply = input(f"{prompt} [{hint}] ").strip().lower()
            if not reply and default:
                return default
            if reply and reply[0] in choices:
                return reply[0]

    while True:
        console.print(f"[yellow][?][/yellow] {prompt} [{hint}] ", end="")
        try:
            reply = _read_key().lower()
        except Exception:
            reply = input().strip().lower()
        console.print(reply)
        if reply == "\r" or reply == "\n":
            if default:
                return default
            continue
        if reply in choices:
            return reply
