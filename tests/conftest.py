"""Shared helpers for the test suite.

Unit tests import `toolboxcli` directly. Integration tests run the real commands as
subprocesses (`python -m toolboxcli.<script>.cli`) against throwaway folders, with HOME
pointed into the test's tmp dir so anything sent to the Trash lands there too.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def needs(*tools: str):
    """Skip a test when an external binary it shells out to isn't installed."""
    missing = [t for t in tools if shutil.which(t) is None]
    return pytest.mark.skipif(bool(missing), reason=f"not installed: {', '.join(missing)}")


@pytest.fixture
def sandbox_env(tmp_path: Path) -> dict[str, str]:
    """Environment for a command run: HOME/XDG dirs inside tmp_path, so a trashed file
    goes to a Trash inside the test folder rather than the real one, and no colour codes
    so output can be matched as plain text."""
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        XDG_DATA_HOME=str(home / ".local" / "share"),
        NO_COLOR="1",
        TERM="dumb",
        COLUMNS="200",
        PYTHONUTF8="1",
    )
    return env


@pytest.fixture
def run_cli(sandbox_env):
    """Run a toolbox command's module in a subprocess: run_cli("vidlib", [...])."""

    def run(script: str, args: list[str], cwd: Path | None = None,
            env_extra: dict[str, str] | None = None,
            stdin: str | None = None) -> subprocess.CompletedProcess:
        module = script.replace("-", "")
        env = dict(sandbox_env, **(env_extra or {}))
        return subprocess.run(
            [sys.executable, "-m", f"toolboxcli.{module}.cli", *args],
            cwd=cwd, env=env, input=stdin if stdin is not None else "",
            capture_output=True, text=True, encoding="utf-8", timeout=300,
        )

    return run


@pytest.fixture
def make_video():
    """Generate a tiny real video with ffmpeg: make_video(path, "1280x720", bit10=False)."""

    def make(path: Path, size: str = "1280x720", seconds: int = 2, bit10: bool = False,
             subtitle: bool = False) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-v", "error", "-y",
               "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10",
               "-f", "lavfi", "-i", "anoisesrc"]
        if subtitle:
            srt = path.with_suffix(".tmp.srt")
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
            cmd += ["-i", str(srt), "-c:s", "mov_text"]
        cmd += ["-t", str(seconds), "-shortest"]
        if bit10:
            cmd += ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=none"]
        cmd.append(str(path))
        subprocess.run(cmd, check=True, capture_output=True)
        if subtitle:
            srt.unlink()
        return path

    return make


# Four test patterns that fingerprint unlike each other on every frame.
SOURCES = {
    "testsrc2": "testsrc2=s={size}:d={d}",
    "mandelbrot": "mandelbrot=s={size},trim=duration={d}",
    "life": "life=s={size}:ratio=0.3:seed=4,trim=duration={d}",
    "cellauto": "cellauto=s={size}:rule=110:seed=5,trim=duration={d}",
}


@pytest.fixture
def clip():
    """clip(path, source, size="320x180", seconds=12): a short video of a test pattern."""

    def make(path: Path, source: str, size: str = "320x180", seconds: int = 12) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", SOURCES[source].format(size=size, d=seconds),
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-t", str(seconds), str(path)], check=True)
        return path
    return make
