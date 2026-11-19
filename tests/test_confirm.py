"""_common.confirm: piped/closed input, and real keypresses through a pseudo-terminal."""

from __future__ import annotations

import io
import os
import re
import sys
import time

import pytest

from toolboxcli._common.confirm import confirm


def _piped(monkeypatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


@pytest.mark.parametrize("text, default, expected", [
    ("y\n", None, "y"), ("No thanks\n", None, "n"), ("\n", "y", "y"), ("x\ny\n", None, "y"),
])
def test_piped_answers(monkeypatch, text, default, expected):
    _piped(monkeypatch, text)
    assert confirm("Go?", default=default) == expected


@pytest.mark.parametrize("default", [None, "y"])
def test_closed_input_is_a_no_even_when_enter_means_yes(monkeypatch, default):
    _piped(monkeypatch, "")
    assert confirm("Download them too?", default=default) == "n"


def test_closed_input_without_a_no_choice_uses_the_default_or_stops(monkeypatch):
    _piped(monkeypatch, "")
    assert confirm("Pick", choices="ab", default="b") == "b"
    _piped(monkeypatch, "")
    with pytest.raises(SystemExit):
        confirm("Pick", choices="ab")


# ── through a pseudo-terminal (raw-mode single keypresses) ─────────────

pty = pytest.importorskip("pty") if sys.platform != "win32" else None
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pty")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _keypress(keys: bytes, prompt: str = "Proceed?", default: str = "n") -> str:
    """Run confirm() in a child attached to a pty, send `keys`, return what it printed."""
    import select

    child = (
        "import os, sys\n"
        "from toolboxcli._common.confirm import confirm\n"
        "try:\n"
        "    print('RESULT', repr(confirm(sys.argv[1], default=sys.argv[2])), flush=True)\n"
        "except KeyboardInterrupt:\n"
        "    print('RESULT interrupted', flush=True)\n"
    )
    pid, fd = pty.fork()
    if pid == 0:  # child: a fresh interpreter, so pytest's output capture isn't inherited
        os.environ["NO_COLOR"] = "1"
        os.execv(sys.executable, [sys.executable, "-c", child, prompt, default])

    out = b""
    deadline = time.monotonic() + 10
    sent = False
    while time.monotonic() < deadline:
        if select.select([fd], [], [], 0.2)[0]:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            plain = ANSI_RE.sub("", out.decode(errors="replace"))
            if not sent and re.search(r"\[[a-zA-Z](/[a-zA-Z])+\] $", plain):
                # The hint is printed just before the terminal enters raw mode, and
                # entering it discards pending input — so give it a moment first, or
                # the key is lost (and Ctrl+C would still be the terminal's own).
                time.sleep(0.5)
                os.write(fd, keys)
                sent = True
            if b"RESULT" in out and out.endswith(b"\n"):
                break
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return ANSI_RE.sub("", out.decode(errors="replace"))


@posix_only
def test_keypress_answers_without_enter():
    assert "RESULT 'y'" in _keypress(b"y")


@posix_only
def test_enter_takes_the_default():
    assert "RESULT 'n'" in _keypress(b"\r")


@posix_only
def test_ctrl_c_interrupts_instead_of_being_swallowed():
    assert "RESULT interrupted" in _keypress(b"\x03")


@posix_only
def test_ctrl_d_answers_no():
    assert "RESULT 'n'" in _keypress(b"\x04", default="y")


@posix_only
def test_hint_and_bracketed_filenames_are_visible():
    out = _keypress(b"y", prompt='Run: addsub -u "Clip [1080p].mkv"?', default="n")
    assert "[y/N]" in out
    assert "Clip [1080p].mkv" in out
