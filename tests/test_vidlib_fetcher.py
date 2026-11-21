"""vidlib's download engine and progress parsing, against a fake yt-dlp."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from toolboxcli.vidlib import fetcher as engine
from toolboxcli.vidlib.fetcher import Progress, parse_progress, rate_bytes


@pytest.mark.parametrize("line, expected", [
    ("[download]  45.3% of ~  10.00MiB at    1.23MiB/s ETA 00:05 (frag 3/10)",
     Progress(45.3, "1.23MiB/s", "00:05")),
    ("[download] 100% of   10.00MiB in 00:00:02 at 4.5MiB/s", Progress(100.0, "4.5MiB/s", "")),
    ("[download]   0.0% of 10.00MiB at Unknown B/s ETA Unknown", Progress(0.0, "", "")),
    ("[#2089b0 400.0KiB/33.2MiB(1%) CN:16 DL:4.2MiB ETA:4m50s]", Progress(1.0, "4.2MiB/s", "4m50s")),
    ("[#2089b0 33.2MiB/33.2MiB(100%) CN:1 DL:0B]", Progress(100.0, "0B/s", "")),
    ("[#2089b0 0B/0B CN:1 DL:0B]", None),
    ("[info] Downloading 1 format(s): 22", None),
    ("ERROR: Unsupported URL", None),
])
def test_parse_progress(line, expected):
    assert parse_progress(line) == expected


@pytest.mark.parametrize("speed, rate", [
    ("4.2MiB/s", 4.2 * 1024**2), ("1.5MB/s", 1.5e6), ("800KiB/s", 800 * 1024),
    ("12B/s", 12), ("0B/s", 0), ("Unknown B/s", 0), ("", 0),
])
def test_rate_bytes(speed, rate):
    assert rate_bytes(speed) == pytest.approx(rate)


def test_build_command(tmp_path):
    folder = tmp_path / "50% off"
    cmd = engine.build_command("https://x/a", folder, "100% Done")
    assert cmd[0] == "yt-dlp" and cmd[-1] == "https://x/a" and "--newline" in cmd
    assert cmd[cmd.index("-o") + 1] == f"{str(folder).replace('%', '%%')}/100%% Done.%(ext)s"
    assert engine.output_template(folder, None).endswith("/%(title)s.%(ext)s")


def test_finished_files_skip_partials(tmp_path):
    for name in ("A.mp4", "A.mp4.part", "A.f137.mp4.ytdl", "A.mp4.aria2", "A.en.vtt"):
        (tmp_path / name).write_text("x")
    assert [p.name for p in engine.finished_files(tmp_path)] == ["A.en.vtt", "A.mp4"]
    assert engine.finished_files(tmp_path / "missing") == []


# ── The engine, driving a fake yt-dlp ──────────────────────────────────

FAKE_YTDLP = """\
#!{python}
import pathlib, sys, time
url = sys.argv[-1]
if "fail" in url:
    print("ERROR: simulated failure", flush=True)
    sys.exit(2)
for pct in (10, 50, 100):
    print(f"[download]  {{pct}}.0% of 1.00MiB at 2.00MiB/s ETA 00:0{{(100 - pct) // 50}}", flush=True)
    time.sleep(0.6 if "slow" in url else 0.01)
template = sys.argv[sys.argv.index("-o") + 1]
pathlib.Path(template.replace("%(ext)s", "mp4").replace("%(title)s", "Video Title")
             .replace("%%", "%")).write_text(url)
"""

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fake yt-dlp is a POSIX script")


@pytest.fixture
def fake_ytdlp(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "yt-dlp"
    script.write_text(FAKE_YTDLP.format(python=sys.executable))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


@posix_only
def test_progress_is_reported_and_the_file_written(fake_ytdlp):
    seen: list[float] = []
    started: list[object] = []
    done: list[engine.Job] = []
    lock = threading.Lock()

    def on_line(job, line, progress):
        if progress:
            with lock:
                seen.append(progress.percent)

    with engine.Engine(2, on_line=on_line, on_start=lambda j: started.append(j.id),
                       on_done=done.append) as eng:
        eng.submit(engine.Job("a", "https://x/a", fake_ytdlp / "a",
                              "Alice - Day 1 - Lakes")).result()
    assert seen == [10.0, 50.0, 100.0]
    assert started == ["a"] and done[0].returncode == 0
    assert (fake_ytdlp / "a" / "Alice - Day 1 - Lakes.mp4").read_text() == "https://x/a"


@posix_only
def test_a_failure_keeps_its_last_lines(fake_ytdlp):
    with engine.Engine(1) as eng:
        job = eng.submit(engine.Job(1, "https://x/fail", fake_ytdlp / "1")).result()
    assert job.returncode == 2
    assert list(job.tail) == ["ERROR: simulated failure"]


@posix_only
def test_cancel_stops_a_running_download(fake_ytdlp):
    running = threading.Event()
    with engine.Engine(1, on_start=lambda _job: running.set()) as eng:
        job = engine.Job(1, "https://x/slow", fake_ytdlp / "1", "Slow")
        future = eng.submit(job)
        assert running.wait(5)
        time.sleep(0.2)
        eng.cancel(job)
        future.result(timeout=10)
    assert job.cancelled and job.returncode not in (0, None)
    assert not (fake_ytdlp / "1" / "Slow.mp4").exists()


@posix_only
def test_a_job_no_longer_wanted_never_starts_but_still_reports_its_end(fake_ytdlp):
    started: list[object] = []
    done: list[object] = []
    with engine.Engine(1, on_start=lambda j: started.append(j.id),
                       on_done=lambda j: done.append(j.id)) as eng:
        job = eng.submit(engine.Job(1, "https://x/a", fake_ytdlp / "1", "A",
                                    still_wanted=lambda: False)).result()
    assert job.cancelled and job.returncode is None and started == [] and done == [1]
    assert not (fake_ytdlp / "1").exists()


@posix_only
def test_leaving_the_engine_on_an_error_cancels_everything(fake_ytdlp):
    running = threading.Event()
    jobs = [engine.Job(i, f"https://x/slow{i}", fake_ytdlp / str(i), f"S{i}") for i in range(3)]
    with pytest.raises(KeyboardInterrupt), \
            engine.Engine(1, on_start=lambda _j: running.set()) as eng:
        for job in jobs:
            eng.submit(job)
        assert running.wait(5)
        raise KeyboardInterrupt
    assert all(job.cancelled for job in jobs)
    assert jobs[1].returncode is None and jobs[2].returncode is None   # queued: never ran


def test_aria2c_reports_progress_every_second_and_its_summary_noise_is_dropped():
    cmd = engine.build_command("https://x/a", Path("/tmp/x"), "A")
    args = cmd[cmd.index("--external-downloader-args") + 1]
    assert args.startswith("aria2c:") and "--summary-interval=1" in args
    assert "--show-console-readout=false" in args
    for noise in (" *** Download Progress Summary as of Sat Nov 21 21:59:37 2026 *** ",
                  "=" * 79, "-" * 79, "FILE: /x/a.mp4.part"):
        assert engine._SUMMARY_NOISE.match(noise.strip())
    assert parse_progress("[#54e45f 304KiB/1.9MiB(15%) CN:1 DL:157KiB ETA:10s]") == Progress(
        15.0, "157KiB/s", "10s")
