"""
prunelib — Review the biggest, oldest or least-watched files in a media library and
delete the ones you no longer want.

Scans the current directory and everything below it, ranks the video files by
[type], and lists the top [count] of them. You then pick which to delete, opening
any of them in your default player first to check. count and type can be given
in either order; they default to 10 and size.

Types:
    size     biggest first
    oldest   oldest modified date first
    stale    least recently opened first (last-access time). Unreliable where the
             drive doesn't record access times — Windows turns that off on large
             NTFS volumes — and prunelib warns when it looks that way. Anything
             that reads a whole file (Jellyfin trickplay generation, a backup
             tool) also counts as opening it.

At the prompt:
    1 3 5-7   toggle those rows in or out of the selection
    a / n     select all / none
    o 3       open row 3 in its default app (several rows work too: o 1-3)
    d         delete the selection, after listing everything that will go and
              asking to confirm
    q         finish

Deleting a video also deletes its sidecar files — anything in the same folder
named after it, such as Movie.en.srt, Movie.nfo, Movie-thumb.jpg or
Movie.trickplay — and then any folder left holding nothing but Jellyfin
metadata, artwork or subtitles (so a finished show's folder goes too, once its
last season is deleted). All of it is listed before you confirm.

Files go to the Recycle Bin/Trash, so nothing frees disk space until you empty
it. On a network share there may be no Recycle Bin: depending on the server
the file then lands in the share's own recycle folder (Synology #recycle) or
is deleted outright. -p/--permanent deletes outright, after you type the
number of items to confirm.

History: files you were shown and kept are recorded in .prunelib-history.json
in the directory you ran prunelib from, and skipped on later runs, so each run
moves on to fresh suggestions. A file comes back if its size or modified date
changes (say, optimiselib re-encoded it). History is only written when you
finish with q; Ctrl+C leaves it untouched. -r/--reset-history starts over.

By default only video files are considered; -a/--all-files ranks every file.
Dot-folders and NAS recycle-bin/snapshot folders are never scanned.

Usage:
    prunelib [options] [count] [type]
    prunelib
    prunelib 10 size
    prunelib oldest 25
    prunelib 20 stale -p
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from rich import box
from rich.markup import escape
from rich.table import Table

from toolboxcli._common.confirm import confirm
from toolboxcli._common.console import console, die, error, info, ok, warn
from toolboxcli._common.humanize import human_size
from toolboxcli._common.progress import spinner
from toolboxcli._common.tooling import detached_kwargs
from toolboxcli._common.trash import move_to_trash
from toolboxcli.prunelib import core
from toolboxcli.prunelib.core import Candidate

PROMPT_HELP = (
    "[dim]numbers/ranges toggle · [bold]a[/bold] all · [bold]n[/bold] none · "
    "[bold]o N[/bold] open · [bold]d[/bold] delete selected · [bold]q[/bold] finish[/dim]"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prunelib",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "spec", nargs="*", metavar="count|type",
        help=f"How many files to list (default {core.DEFAULT_COUNT}) and how to rank them: "
             f"{', '.join(core.SORTS)} (default {core.DEFAULT_SORT}), in either order",
    )
    parser.add_argument("-a", "--all-files", action="store_true",
                        help="Rank every file, not just videos")
    parser.add_argument("-p", "--permanent", action="store_true",
                        help="Delete outright instead of moving to the Recycle Bin/Trash")
    parser.add_argument("-r", "--reset-history", action="store_true",
                        help="Forget which files were already reviewed in this directory")
    return parser


# ── Scanning ─────────────────────────────────────────────────────────

def scan(root: Path, all_files: bool) -> list[Candidate]:
    cands: list[Candidate] = []
    with spinner(transient=True) as progress:
        task = progress.add_task(f"Scanning {root} …", total=None)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not core.skip_dir(d)]
            for name in filenames:
                if name.startswith("."):
                    continue
                if not all_files and Path(name).suffix.lower() not in core.VIDEO_EXTS:
                    continue
                path = Path(dirpath) / name
                try:
                    if path.is_symlink():
                        continue  # deleting a link frees nothing
                    st = path.stat()
                except OSError:
                    continue
                rel = path.relative_to(root).as_posix()
                cands.append(Candidate(rel, st.st_size, st.st_mtime, st.st_atime))
                if len(cands) % 200 == 0:
                    progress.update(task, description=f"Scanned {len(cands)} files …")
    return cands


# ── History ──────────────────────────────────────────────────────────

def history_path(root: Path) -> Path:
    return root / core.HISTORY_NAME


def load_history(root: Path) -> dict[str, dict]:
    p = history_path(root)
    try:
        return core.parse_history(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except OSError as e:
        warn(f"could not read {p.name} ({e}); starting with an empty history")
        return {}


def save_history(root: Path, reviewed: dict[str, dict]) -> None:
    # Drop entries for files that no longer exist, so the file doesn't grow forever.
    reviewed = {k: v for k, v in reviewed.items() if (root / k).exists()}
    p = history_path(root)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(core.dump_history(reviewed), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        error(f"could not save review history to {p}: {e}")


# ── Deletion plan ────────────────────────────────────────────────────

@dataclass
class Plan:
    items: list[Path] = field(default_factory=list)  # top-level paths, none nested in another
    sizes: dict[Path, int] = field(default_factory=dict)
    file_counts: dict[Path, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.sizes.values())


def _sweepable(d: Path, planned: set[Path]) -> bool:
    """True if, once ``planned`` is gone, ``d`` holds only Jellyfin leftovers.

    Subfolders are always looked inside, never waved through by name: a folder called
    ``metadata`` holding a video must keep its parent, and whatever a swept folder
    contains has to be something the confirmation listing accounts for."""
    try:
        entries = list(os.scandir(d))
    except OSError:
        return False
    for e in entries:
        p = d / e.name
        if p in planned:
            continue
        if e.is_dir(follow_symlinks=False):
            if _sweepable(p, planned):
                continue
            return False
        if not core.is_leftover(e.name, False):
            return False
    return True


def _measure(p: Path) -> tuple[int, int]:
    """(bytes, file count) of a file or a whole folder."""
    if not p.is_dir() or p.is_symlink():
        try:
            return p.lstat().st_size, 1
        except OSError:
            return 0, 1
    total = count = 0
    for dirpath, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += (Path(dirpath) / f).lstat().st_size
            except OSError:
                pass
            count += 1
    return total, count


def build_plan(root: Path, videos: list[Path]) -> Plan:
    planned: set[Path] = set(videos)
    for v in videos:
        try:
            siblings = os.listdir(v.parent)
        except OSError:
            continue
        for s in core.sidecar_names(v.name, siblings, lambda n, d=v.parent: (d / n).is_dir()):
            sc = v.parent / s
            # A sidecar folder (Movie.trickplay) goes only if it holds nothing but leftovers.
            if sc.is_dir() and not sc.is_symlink() and not _sweepable(sc, set()):
                continue
            planned.add(sc)

    # Walk up from each emptied folder, deepest first, sweeping folders left holding
    # only leftovers. Never the scan root itself, nor anything outside it.
    for d in sorted({v.parent for v in videos}, key=lambda p: len(p.parts), reverse=True):
        while d != root and root in d.parents and d not in planned:
            if not _sweepable(d, planned):
                break
            planned.add(d)
            d = d.parent

    plan = Plan()
    for p in sorted(planned):
        if any(a in planned for a in p.parents):
            continue
        plan.items.append(p)
        plan.sizes[p], plan.file_counts[p] = _measure(p)
    return plan


def show_plan(root: Path, plan: Plan, videos: set[Path], permanent: bool) -> None:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    table.add_column("What", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("Path", overflow="fold")
    for p in plan.items:
        rel = p.relative_to(root).as_posix()
        if not (p.is_dir() and not p.is_symlink()):
            table.add_row("video" if p in videos else "sidecar", human_size(plan.sizes[p]), escape(rel))
            continue
        # A swept folder holds only selected videos plus leftover-type files (that's
        # what made it sweepable), so naming the videos and counting the rest is complete.
        inside = sorted(v for v in videos if p in v.parents)
        others = plan.file_counts[p] - len(inside)
        table.add_row("folder", human_size(plan.sizes[p]), escape(rel + "/"))
        for v in inside:
            table.add_row("", "", f"[dim]  └ {escape(v.relative_to(p).as_posix())}[/dim]")
        if others:
            table.add_row("", "", f"[dim]  └ + {others} metadata/artwork/subtitle "
                                  f"file{'s' if others != 1 else ''}[/dim]")
    console.print()
    verb = "Permanently delete" if permanent else "Move to the Recycle Bin/Trash"
    console.print(f"[bold]{verb}:[/bold]")
    console.print(table)
    console.print(f"  [bold]{len(plan.items)} item(s), {human_size(plan.total)}[/bold]")


def remove(p: Path, permanent: bool) -> None:
    if not permanent:
        # absolute(), not resolve(): a symlinked sidecar must trash the link, never
        # the file it points at.
        move_to_trash(p.absolute())
    elif p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def delete_selected(root: Path, selected: list[Candidate], permanent: bool) -> set[str]:
    """Plan, confirm and delete. Returns the rel paths of candidates now gone."""
    videos = [root / c.rel for c in selected]
    plan = build_plan(root, videos)
    show_plan(root, plan, set(videos), permanent)

    n = len(plan.items)
    if permanent:
        try:
            reply = console.input(
                f"[yellow][?][/yellow] This can't be undone. Type [bold]{n}[/bold] to delete "
                f"{n} item(s), anything else to cancel: "
            ).strip()
        except EOFError:  # input closed: nobody typed the count, so nothing is deleted
            console.print()
            reply = ""
        if reply != str(n):
            info("Cancelled — nothing deleted.")
            return set()
    elif confirm("Go ahead?", "yn", default="n") != "y":
        info("Cancelled — nothing deleted.")
        return set()

    done = 0
    freed = 0
    for p in plan.items:
        try:
            remove(p, permanent)
        except Exception as e:  # trash backends raise a mix of OSError/RuntimeError/CalledProcessError
            error(f"could not delete {escape(p.relative_to(root).as_posix())}: {escape(str(e))}")
            continue
        done += 1
        freed += plan.sizes[p]

    if done:
        if permanent:
            ok(f"Deleted {done} item(s), freeing {human_size(freed)}.")
        else:
            ok(f"Moved {done} item(s) ({human_size(freed)}) to the Recycle Bin/Trash — "
               "empty it to actually free the space.")
    return {c.rel for c in selected if not (root / c.rel).exists()}


# ── Review loop ──────────────────────────────────────────────────────

def open_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows-only
            return
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if shutil.which(opener) is None:
            warn(f"can't open files here: '{opener}' is not on PATH")
            return
        subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **detached_kwargs())
    except OSError as e:
        error(f"could not open {escape(path.name)}: {e}")


def _fmt_date(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def render(rows: list[Candidate], selected: set[str], sort: str) -> None:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    table.add_column("", width=1)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Modified")
    if sort == "stale":
        table.add_column("Last opened")
    table.add_column("File", overflow="fold")
    for i, c in enumerate(rows, 1):
        mark = "[green]✔[/green]" if c.rel in selected else ""
        cells = [mark, str(i), human_size(c.size), _fmt_date(c.mtime)]
        if sort == "stale":
            cells.append(_fmt_date(c.atime))
        cells.append(escape(c.rel))
        table.add_row(*cells, style="bold" if c.rel in selected else None)
    console.print()
    console.print(table)
    if selected:
        chosen = [c for c in rows if c.rel in selected]
        console.print(f"  [green]{len(chosen)} selected, "
                      f"{human_size(sum(c.size for c in chosen))}[/green]")
    console.print(f"  {PROMPT_HELP}")


def review(root: Path, rows: list[Candidate], sort: str, permanent: bool) -> list[Candidate]:
    """Run the interactive loop. Returns the rows still present when the user finishes."""
    selected: set[str] = set()
    redraw = True
    while rows:
        if redraw:
            render(rows, selected, sort)
        redraw = False
        try:
            line = console.input("[yellow]>[/yellow] ").strip()
        except EOFError:
            break
        cmd = line.lower()
        if not cmd:
            redraw = True
        elif cmd in ("q", "quit", "exit"):
            break
        elif cmd in ("?", "h", "help"):
            console.print(f"  {PROMPT_HELP}")
        elif cmd == "a":
            selected = {c.rel for c in rows}
            redraw = True
        elif cmd == "n":
            selected.clear()
            redraw = True
        elif cmd.startswith("o"):
            try:
                idxs = core.parse_selection(cmd[1:], len(rows))
            except ValueError as e:
                warn(str(e))
                continue
            if not idxs:
                warn("which row? e.g. o 3")
            for i in dict.fromkeys(idxs):
                info(f"Opening {escape(rows[i].rel)}")
                open_file(root / rows[i].rel)
        elif cmd == "d":
            chosen = [c for c in rows if c.rel in selected]
            if not chosen:
                warn("nothing selected — type row numbers first, e.g. 1 3 5-7")
                continue
            gone = delete_selected(root, chosen, permanent)
            rows = [c for c in rows if c.rel not in gone]
            selected -= gone
            redraw = True
        else:
            try:
                idxs = core.parse_selection(cmd, len(rows))
            except ValueError as e:
                warn(f"{e} — type ? for help")
                continue
            for i in dict.fromkeys(idxs):
                selected ^= {rows[i].rel}
            redraw = True
    return rows


def main() -> None:
    args = build_parser().parse_args()
    try:
        count, sort = core.parse_positionals(args.spec)
    except ValueError as e:
        die(str(e))

    root = Path.cwd()
    if args.reset_history and history_path(root).exists():
        history_path(root).unlink()
        info(f"Cleared {core.HISTORY_NAME}")
    reviewed = load_history(root)

    try:
        cands = scan(root, args.all_files)
        kind = "files" if args.all_files else "videos"
        if not cands:
            ok(f"No {kind} found under {root}")
            return

        fresh = [c for c in cands if not core.is_reviewed(reviewed, c)]
        hidden = len(cands) - len(fresh)
        rows = core.rank(fresh, sort)[:count]

        summary = (f"Scanned {len(cands)} {kind} ({human_size(sum(c.size for c in cands))}); "
                   f"showing {len(rows)} by {sort} ({core.SORTS[sort]})")
        if hidden:
            summary += f" — {hidden} already reviewed are hidden (-r to reset)"
        info(summary)
        if sort == "stale" and core.atime_looks_unrecorded(cands):
            warn("no file here has been opened since it was last modified — this drive "
                 "probably isn't recording access times, so 'stale' is effectively "
                 "ordering by the date files were copied in")
        if not rows:
            ok("Nothing left to review here. Run with -r to start over.")
            return

        remaining = review(root, rows, sort, args.permanent)
    except KeyboardInterrupt:
        console.print()
        warn("Interrupted — review history not updated.")
        sys.exit(130)

    if remaining:
        save_history(root, core.mark_reviewed(reviewed, remaining, dt.date.today().isoformat()))
        info(f"Marked {len(remaining)} kept file(s) as reviewed in {core.HISTORY_NAME}")


if __name__ == "__main__":
    main()
