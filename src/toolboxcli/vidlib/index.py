"""Thumbnails, hover previews and content fingerprints, made ahead of time.

One ffmpeg run per video makes all three: it seeks to the core.fp_positions() of the video,
and each frame it lands on goes both into the hover filmstrip (side by side, one JPEG) and,
shrunk to 9x8 greyscale, into the fingerprint (core.dhash); one more seek, a tenth of the
way in, is the thumbnail. A fast seek costs the same however long the video is.

Images are cached in `.vidlib-cache/thumbs/` under a key made from the file's size and
modified time, not its path: renaming or filing a video keeps its images, and a re-encode
(new size and mtime) gets fresh ones. prune_cache() drops the images no video has any more.

The Indexer is the web page's background worker: it walks every video still missing
something, one at a time and at low priority, so browsing finds everything ready; `vidlib
-I` does the same pass in the terminal and exits.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from toolboxcli._common.console import warn
from toolboxcli._common.tooling import detached_kwargs
from toolboxcli.vidlib import core
from toolboxcli.vidlib.db import Video

CACHE_DIR = ".vidlib-cache"
THUMB_WIDTH = 480
STRIP_FRAMES = core.FP_FRAMES       # the filmstrip's frames are the fingerprint's
STRIP_WIDTH = 320
RENDER_TIMEOUT = 300                # seconds for one video's ffmpeg run
IDLE_SECONDS = 30                   # how long the Indexer rests once everything is done
_FRAME_BYTES = core.FP_WIDTH * core.FP_HEIGHT


def thumbs_dir(root: Path) -> Path:
    return root / CACHE_DIR / "thumbs"


def cache_key(size: int, mtime: float) -> str:
    """The images' name for a file with this size and mtime."""
    return hashlib.sha1(f"{size}\0{mtime:.6f}".encode()).hexdigest()


def cache_paths(root: Path, size: int, mtime: float) -> tuple[Path, Path]:
    """(thumbnail, filmstrip) for a file with this size and mtime."""
    key = cache_key(size, mtime)
    folder = thumbs_dir(root)
    return folder / f"{key}.jpg", folder / f"{key}-strip{STRIP_FRAMES}.jpg"


def _low_priority(cmd: list[str]) -> tuple[list[str], dict[str, Any]]:
    """*cmd* run below normal priority, and the Popen kwargs for it. `nice` rather than
    preexec_fn, which isn't safe to use from a thread."""
    kwargs: dict[str, Any] = dict(detached_kwargs())
    if os.name == "nt":
        kwargs["creationflags"] = (kwargs.get("creationflags", 0)
                                   | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        return cmd, kwargs
    nice = shutil.which("nice")
    return ([nice, "-n", "19", *cmd] if nice else cmd), kwargs


class Runner:
    """Runs ffmpeg for a render, so that whoever owns it can stop the current run (the
    Indexer on quitting) instead of waiting for it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self.stopped = False

    def run(self, cmd: list[str], timeout: float = RENDER_TIMEOUT) -> bytes | None:
        """ffmpeg's stdout if it exited 0; None if it failed, timed out or was stopped."""
        cmd, kwargs = _low_priority(cmd)
        with self._lock:
            if self.stopped:
                return None
            try:
                # Bytes in, bytes out; the kwargs dict hides that from the type checker.
                proc = cast("subprocess.Popen[bytes]", subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, **kwargs))
            except OSError:
                return None
            self._proc = proc
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return None
        finally:
            with self._lock:
                self._proc = None
        return out if proc.returncode == 0 else None

    def stop(self) -> None:
        with self._lock:
            self.stopped = True
            if self._proc is not None:
                self._proc.kill()


def _seek(at: float, file: Path) -> list[str]:
    return ["-ss", f"{at:.3f}", "-i", str(file)]


def _fingerprint(raw: bytes | None) -> str:
    if raw is None or len(raw) != _FRAME_BYTES * STRIP_FRAMES:
        return ""
    return core.format_fingerprint([core.dhash(raw[i * _FRAME_BYTES:(i + 1) * _FRAME_BYTES])
                                    for i in range(STRIP_FRAMES)])


def render(file: Path, duration: float, thumb: Path | None = None, strip: Path | None = None,
           runner: Runner | None = None) -> str:
    """Make the thumbnail and filmstrip (each only when a path is given) and return the
    fingerprint ('' if it couldn't be made). One ffmpeg run does it all; if that fails —
    a seek past the real end of a file shorter than it claims stops the whole run — each
    frame is read on its own instead, a missing one counting as flat."""
    runner = runner or Runner()
    if duration <= 0:
        if thumb is not None:
            _single_thumb(file, 0.0, thumb, runner)
        return ""
    positions = core.fp_positions(duration)
    with tempfile.TemporaryDirectory(prefix=".render-", dir=_work_dir(thumb, strip)) as work:
        tmp_thumb, tmp_strip = Path(work) / "thumb.jpg", Path(work) / "strip.jpg"
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y"]
        for at in positions:
            cmd += _seek(at, file)
        graph: list[str] = []
        hash_scale = f"scale={core.FP_WIDTH}:{core.FP_HEIGHT}:flags=area,format=gray"
        for i in range(STRIP_FRAMES):
            first = f"[{i}:v:0]trim=end_frame=1,setpts=PTS-STARTPTS"
            if strip is None:
                graph.append(f"{first},{hash_scale}[H{i}]")
                continue
            graph.append(f"{first},split=2[s{i}][h{i}]")
            graph.append(f"[s{i}]scale={STRIP_WIDTH}:-2,setsar=1,format=yuvj420p[S{i}]")
            graph.append(f"[h{i}]{hash_scale}[H{i}]")
        graph.append("".join(f"[H{i}]" for i in range(STRIP_FRAMES))
                     + f"vstack=inputs={STRIP_FRAMES}[hash]")
        if strip is not None:
            graph.append("".join(f"[S{i}]" for i in range(STRIP_FRAMES))
                         + f"hstack=inputs={STRIP_FRAMES}[strip]")
        if thumb is not None:
            cmd += _seek(duration * 0.1, file)
            graph.append(f"[{STRIP_FRAMES}:v:0]trim=end_frame=1,scale={THUMB_WIDTH}:-2,"
                         "setsar=1,format=yuvj420p[thumb]")
        cmd += ["-filter_complex", ";".join(graph)]
        if strip is not None:
            cmd += ["-map", "[strip]", "-frames:v", "1", "-q:v", "6", str(tmp_strip)]
        if thumb is not None:
            cmd += ["-map", "[thumb]", "-frames:v", "1", "-q:v", "5", str(tmp_thumb)]
        cmd += ["-map", "[hash]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray",
                "pipe:1"]
        raw = runner.run(cmd)
        fingerprint = _fingerprint(raw)
        if fingerprint and _good(tmp_strip, strip) and _good(tmp_thumb, thumb):
            _place(tmp_strip, strip)
            _place(tmp_thumb, thumb)
            return fingerprint
        if runner.stopped:
            return ""
        return _render_slowly(file, duration, thumb, strip, runner, Path(work))


def _work_dir(*outs: Path | None) -> Path | None:
    for out in outs:
        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            return out.parent
    return None


def _good(tmp: Path, wanted: Path | None) -> bool:
    return wanted is None or (tmp.is_file() and tmp.stat().st_size > 0)


def _place(tmp: Path, dest: Path | None) -> None:
    if dest is not None:
        os.replace(tmp, dest)


def make_thumbnail(file: Path, duration: float, thumb: Path,
                   runner: Runner | None = None) -> bool:
    """Just the thumbnail, from a tenth of the way in (the first frame for a file shorter
    than its recorded length): what the page asks for first, so it doesn't wait for a whole
    render — the indexer makes the rest."""
    runner = runner or Runner()
    return any(_single_thumb(file, at, thumb, runner)
               for at in dict.fromkeys((max(duration * 0.1, 0.0), 0.0)))


def _single_thumb(file: Path, at: float, thumb: Path, runner: Runner) -> bool:
    """One frame as the thumbnail, written in place atomically."""
    thumb.parent.mkdir(parents=True, exist_ok=True)
    tmp = thumb.with_name(f".{thumb.stem}.tmp.jpg")
    runner.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *_seek(at, file),
                "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2", "-q:v", "5", str(tmp)],
               timeout=60)
    if tmp.is_file() and tmp.stat().st_size > 0:
        os.replace(tmp, thumb)
        return True
    tmp.unlink(missing_ok=True)
    return False


def _render_slowly(file: Path, duration: float, thumb: Path | None, strip: Path | None,
                   runner: Runner, work: Path) -> str:
    """render(), a frame at a time: for files whose recorded length overshoots their real
    one, or that a combined run trips over otherwise."""
    hashes: list[int | None] = []
    frames: list[Path] = []
    for i, at in enumerate(core.fp_positions(duration)):
        out = work / f"f{i}.jpg"
        raw = runner.run(["ffmpeg", "-v", "error", "-nostdin", "-y", *_seek(at, file),
                          "-frames:v", "1", "-filter_complex",
                          f"[0:v:0]split=2[a][b];[a]scale={STRIP_WIDTH}:-2,setsar=1[s];"
                          f"[b]scale={core.FP_WIDTH}:{core.FP_HEIGHT}:flags=area,format=gray[h]",
                          "-map", "[s]", "-frames:v", "1", "-q:v", "6", str(out),
                          "-map", "[h]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "gray", "pipe:1"], timeout=60)
        if runner.stopped:
            return ""
        ok = raw is not None and len(raw) == _FRAME_BYTES and out.is_file()
        hashes.append(core.dhash(raw) if ok and raw is not None else None)
        if ok:
            frames.append(out)
    if not frames:
        if thumb is not None:
            _single_thumb(file, 0.0, thumb, runner)   # a frame from the start, at least
        return ""
    if strip is not None:
        # A frame that couldn't be read repeats the nearest one before it (or the first).
        have = frames[0]
        for i in range(STRIP_FRAMES):
            out = work / f"f{i}.jpg"
            if out.is_file():
                have = out
            else:
                shutil.copyfile(have, out)
        tmp = work / "strip.jpg"
        runner.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-start_number", "0",
                    "-i", str(work / "f%d.jpg"), "-vf", f"tile={STRIP_FRAMES}x1",
                    "-frames:v", "1", "-q:v", "6", str(tmp)], timeout=60)
        if tmp.is_file():
            os.replace(tmp, strip)
    if thumb is not None and not _single_thumb(file, duration * 0.1, thumb, runner):
        _single_thumb(file, 0.0, thumb, runner)
    return core.format_fingerprint(hashes)


# ── what's missing, and making it ─────────────────────────────────────

def cached_names(root: Path) -> set[str]:
    try:
        return set(os.listdir(thumbs_dir(root)))
    except OSError:
        return set()


def needs(video: Video, cached: set[str]) -> bool:
    """Whether a video is missing its thumbnail, its filmstrip or its fingerprint. A video
    of unknown length can only have a thumbnail."""
    thumb, strip = cache_paths(Path(), video.size, video.mtime)
    if thumb.name not in cached:
        return True
    return video.duration > 0 and (strip.name not in cached or not video.fingerprint)


def prune_cache(root: Path, videos: list[Video]) -> tuple[int, int]:
    """Delete the cached images no video has any more (deleted, re-encoded, or named by an
    older vidlib after the file's path). Returns (files, bytes) removed."""
    keep: set[str] = set()
    for v in videos:
        thumb, strip = cache_paths(Path(), v.size, v.mtime)
        keep.update((thumb.name, strip.name))
    removed, freed = 0, 0
    folder = thumbs_dir(root)
    for name in cached_names(root):
        if name in keep or name.startswith(".") or not name.endswith(".jpg"):
            continue
        try:
            size = (folder / name).stat().st_size
            (folder / name).unlink()
        except OSError:
            continue
        removed, freed = removed + 1, freed + size
    return removed, freed


def cache_size(root: Path) -> int:
    """Bytes the cached images take."""
    try:
        entries = list(os.scandir(thumbs_dir(root)))
    except OSError:
        return 0
    total = 0
    for entry in entries:
        try:
            total += entry.stat().st_size
        except OSError:
            pass
    return total


class Indexer:
    """The web page's background worker: makes whatever each video is missing, one video
    at a time, then rests until woken (a video filed, a download imported) or
    IDLE_SECONDS pass. *prepare(video)* does one video and is the Library's own, so the
    page's requests and the indexer share one cache and one limit on ffmpeg runs.

    A daemon thread, stopped with stop(): quitting kills the ffmpeg it's running rather
    than wait for it, and Python never waits for the thread itself."""

    def __init__(self, root: Path, videos: Callable[[], list[Video]],
                 prepare: Callable[[Video, Runner], bool],
                 on_change: Callable[[], None] | None = None) -> None:
        self.root = root
        self._videos = videos
        self._prepare = prepare
        self._on_change = on_change
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._runner = Runner()
        self._lock = threading.Lock()
        self._failed: set[tuple[str, int, float]] = set()   # couldn't be read: not retried
        self.total = 0          # videos in this pass
        self.done = 0           # of those, made (or found unreadable)
        self.current = ""
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="vidlib-index", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        self._runner.stop()
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"total": self.total, "done": self.done, "current": self.current}

    def pending(self) -> list[Video]:
        cached = cached_names(self.root)
        return [v for v in self._videos()
                if needs(v, cached) and (v.path, v.size, v.mtime) not in self._failed]

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for name, value in fields.items():
                setattr(self, name, value)
        if self._on_change is not None:
            self._on_change()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                # An unplugged library disk: every file would fail; wait for it instead.
                todo = self.pending() if self.root.is_dir() else []
                if todo:
                    self._set(total=len(todo), done=0)
                    for video in todo:
                        if self._stop.is_set():
                            return
                        self._set(current=video.path)
                        if (not self._prepare(video, self._runner) and not self._stop.is_set()
                                and (self.root / video.path).is_file()):
                            # There, but ffmpeg can't read it: not tried again this run.
                            self._failed.add((video.path, video.size, video.mtime))
                        with self._lock:
                            self.done += 1
                    if not self._stop.is_set():
                        prune_cache(self.root, self._videos())
                self._set(total=0, done=0, current="")
            except Exception as exc:              # noqa: BLE001 — a closed database on
                if self._stop.is_set():           # the way out; anything else is said
                    return                        # once a pass and tried again later
                warn(f"Preparing previews: {type(exc).__name__}: {exc}")
            self._wake.wait(IDLE_SECONDS)
            self._wake.clear()
