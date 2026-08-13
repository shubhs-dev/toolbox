"""Shared HandBrakeCLI plumbing: preset lookup, progress-parsing transcode, file settling.

Extracted from compressvid so optimiselib can reuse the same preset resolution and the same
stderr-scraping progress reporting rather than reimplementing either.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from importlib.resources import files as _resource_files
from pathlib import Path

from rich.table import Table
from rich import box

from toolboxcli._common.console import console, die

HANDBRAKE = "HandBrakeCLI"

DEFAULT_PRESETS_DIR = _resource_files("toolboxcli.compressvid") / "data" / "handbrake-presets"
_presets_dir = DEFAULT_PRESETS_DIR

STABLE_SECS = 2        # pause between file-size polls
STABLE_TIMEOUT = 300   # max seconds to wait for a file to stop growing

# Regex to pull percentage from HandBrakeCLI stderr progress lines
# e.g.  "Encoding: task 1 of 1, 45.23 % (128.34 fps, avg ...)"
HB_PCT_RE = re.compile(r"(\d+\.\d+)\s*%")


def presets_dir():
    """The directory preset stems are resolved against."""
    return _presets_dir


def set_presets_dir(path) -> None:
    """Point preset lookup at a custom directory (compressvid's -P/--presets-dir)."""
    global _presets_dir
    _presets_dir = Path(path)


# ── Utilities ─────────────────────────────────────────────────────────

def wait_stable(path, timeout=STABLE_TIMEOUT):
    """Block until *path*'s size stops changing (file fully written)."""
    prev = -1
    elapsed = 0
    while elapsed < timeout:
        try:
            cur = path.stat().st_size
        except OSError:
            return False
        if cur == prev and cur > 0:
            return True
        prev = cur
        time.sleep(STABLE_SECS)
        elapsed += STABLE_SECS
    return False


# ── Preset helpers ────────────────────────────────────────────────────

def list_presets():
    """Display every JSON preset in a rich table."""
    directory = presets_dir()
    if not directory.is_dir():
        console.print(f"[red]Presets directory not found:[/] {directory}")
        return

    tbl = Table(
        title="Available Presets", box=box.ROUNDED,
        title_style="bold cyan",
    )
    tbl.add_column("Preset name (use with -p)", style="green")
    tbl.add_column("HandBrake label")

    for f in sorted(directory.glob("*.json")):
        try:
            with f.open() as fp:
                d = json.load(fp)
            label = d["PresetList"][0]["PresetName"]
        except (json.JSONDecodeError, KeyError, IndexError):
            label = "[dim](unreadable)[/]"
        tbl.add_row(f.stem if hasattr(f, "stem") else f.name.rsplit(".", 1)[0], label)

    console.print(tbl)


def load_preset(name):
    """Return (preset_file_path, preset_display_name) for a given stem."""
    f = presets_dir() / f"{name}.json"
    if not f.is_file():
        console.print(f"[red bold]ERROR:[/] Preset file not found: {f}\n")
        list_presets()
        sys.exit(1)
    with f.open() as fp:
        d = json.load(fp)
    return str(f), d["PresetList"][0]["PresetName"]


def read_preset_settings(name):
    """Return the raw settings dict of a preset, for callers that need to inspect
    fields (encoder, quality) before deciding what to override on the command line."""
    f = presets_dir() / f"{name}.json"
    if not f.is_file():
        console.print(f"[red bold]ERROR:[/] Preset file not found: {f}\n")
        list_presets()
        sys.exit(1)
    with f.open() as fp:
        return json.load(fp)["PresetList"][0]


# ── Transcode ─────────────────────────────────────────────────────────

def _stream_reader(stream, progress, task_id):
    """Read *stream* in a background thread, parse HandBrake % lines."""
    buf = b""
    while True:
        chunk = stream.read(4096)
        if not chunk:
            break
        buf += chunk
        # Split on \r or \n — HandBrake uses \r to overwrite progress
        parts = re.split(rb"[\r\n]+", buf)
        # Last element is incomplete — keep it for the next read
        buf = parts[-1]
        for part in parts[:-1]:
            line = part.decode("utf-8", errors="replace")
            m = HB_PCT_RE.search(line)
            if m and progress is not None and task_id is not None:
                progress.update(task_id, completed=float(m.group(1)))


def _low_priority_popen_kwargs():
    """Platform-specific Popen kwargs that de-prioritise the child process.

    Only ever softens CPU scheduling — a hardware encode is mostly GPU work, so this
    matters most for the decode/mux side and the CPU-fallback encoder.
    """
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)}
    return {"preexec_fn": lambda: os.nice(19)}


def transcode(src, dst, preset_file, preset_label, progress=None, task_id=None,
              extra_args=None, low_priority=False):
    """Run HandBrakeCLI, stream-parse its progress, return True on success.

    *extra_args* are appended after the preset flags, so they override preset fields
    (that's how the encoder and quality get swapped without editing preset files).
    """
    cmd = [
        HANDBRAKE,
        "--preset-import-file", preset_file,
        "--preset", preset_label,
        "-i", str(src),
        "-o", str(dst),
    ]
    if extra_args:
        cmd.extend(extra_args)

    popen_kwargs = _low_priority_popen_kwargs() if low_priority else {}

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_kwargs,
        )
    except FileNotFoundError:
        die(
            f"'{HANDBRAKE}' not found on PATH. "
            "Install HandBrake CLI: https://handbrake.fr/downloads2.php"
        )

    # Read both stdout and stderr in parallel threads — HandBrake may write
    # progress to either depending on version / platform.
    out_t = threading.Thread(
        target=_stream_reader, args=(proc.stdout, progress, task_id),
        daemon=True,
    )
    err_t = threading.Thread(
        target=_stream_reader, args=(proc.stderr, progress, task_id),
        daemon=True,
    )
    out_t.start()
    err_t.start()

    proc.wait()
    out_t.join(timeout=5)
    err_t.join(timeout=5)

    if progress is not None and task_id is not None:
        progress.update(task_id, completed=100)

    return proc.returncode == 0


def available_encoders():
    """The encoder names this HandBrake build reports under `--encoder`.

    Unlike `ffmpeg -encoders` — which lists everything the build was compiled with, and is
    why _common/encoders.py probes with a throwaway encode instead — HandBrake does runtime
    hardware detection and omits hardware encoders it can't actually use. So parsing --help
    is a real availability signal here and no probe encode is needed.
    """
    try:
        result = subprocess.run(
            [HANDBRAKE, "--help"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return set()

    text = (result.stdout or "") + (result.stderr or "")
    # The block looks like:
    #    -e, --encoder <string>  Select video encoder:
    #                                x264
    #                                nvenc_h265
    #                                ...
    m = re.search(r"--encoder\s+<string>(.*?)(?=\n\s*-{1,2}\w)", text, re.DOTALL)
    if not m:
        return set()
    return {
        token
        for token in re.findall(r"^\s+([A-Za-z0-9_]+)\s*$", m.group(1), re.MULTILINE)
    }
