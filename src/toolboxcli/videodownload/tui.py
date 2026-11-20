"""video-download's terminal UI: paste a URL and the page's title, label the title's words,
and queue the download.

Layout, top to bottom: the URL and title inputs; the word bar, where each word of the title
is labelled people / title / collection (or skipped); the three fields that labelling fills
in, editable by hand; a preview of the filename; and the queue with live progress.

Downloads run on engine.Engine. Its callbacks arrive on worker threads, so they're turned
into messages (post_message is thread-safe and never blocks) rather than touching widgets.
The queue is saved to `.video-download-queue.json` in the folder after every change, so a
restart picks up where it left off.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Input, Label, Static

from toolboxcli.videodownload import engine
from toolboxcli.videodownload.core import (
    SKIP,
    Field,
    KnownNames,
    Labels,
    Progress,
    QueueItem,
    Token,
    auto_label,
    compose,
    dump_queue,
    known_from_queue,
    load_queue,
    parts_from_fields,
    tokenize,
)

QUEUE_FILE = ".video-download-queue.json"

_STYLES: dict[frozenset[str], str] = {
    frozenset(): "dim strike",
    frozenset({"title"}): "white",
    frozenset({"people"}): "bold magenta",
    frozenset({"people", "title"}): "magenta italic",
    frozenset({"collection"}): "bold cyan",
    frozenset({"collection", "title"}): "cyan italic",
}


# ── Queue file ─────────────────────────────────────────────────────────


def load_queue_file(folder: Path) -> list[QueueItem]:
    try:
        return load_queue(json.loads((folder / QUEUE_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def save_queue_file(folder: Path, items: list[QueueItem]) -> None:
    """Write through a temp file, so a crash mid-write never leaves a truncated queue."""
    path = folder / QUEUE_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dump_queue(items), indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# ── The word bar ───────────────────────────────────────────────────────


class WordBar(Widget, can_focus=True):
    """The pasted title's words, each coloured by its labels. Arrow keys move, shift+arrows
    select a run, and p / t / c toggle people / title / collection on the selection; x skips
    it. Separators are shown dimmed and can be labelled like any word."""

    DEFAULT_CSS = "WordBar { height: auto; min-height: 1; padding: 0 1; }"

    BINDINGS = [
        Binding("left", "move(-1)", show=False),
        Binding("right", "move(1)", show=False),
        Binding("shift+left", "extend(-1)", show=False),
        Binding("shift+right", "extend(1)", show=False),
        Binding("home", "jump(0)", show=False),
        Binding("end", "jump(-1)", show=False),
        Binding("p", "toggle('people')", "People"),
        Binding("t", "toggle('title')", "Title"),
        Binding("c", "toggle('collection')", "Collection"),
        Binding("x", "skip", "Skip"),
    ]

    class Changed(Message):
        """The labels changed (by a key press, or a new title)."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.tokens: list[Token] = []
        self.labels: list[Labels] = []
        self.cursor = 0
        self.anchor: int | None = None

    def set_tokens(self, tokens: list[Token], labels: list[Labels]) -> None:
        self.tokens, self.labels = tokens, labels
        self.cursor, self.anchor = 0, None
        self.refresh(layout=True)
        self.post_message(self.Changed())

    def selection(self) -> range:
        if not self.tokens:
            return range(0)
        if self.anchor is None:
            return range(self.cursor, self.cursor + 1)
        lo, hi = sorted((self.anchor, self.cursor))
        return range(lo, hi + 1)

    def _clamp(self, index: int) -> int:
        return max(0, min(index, len(self.tokens) - 1))

    def action_move(self, delta: int) -> None:
        self.cursor, self.anchor = self._clamp(self.cursor + delta), None
        self.refresh()

    def action_extend(self, delta: int) -> None:
        if self.anchor is None:
            self.anchor = self.cursor
        self.cursor = self._clamp(self.cursor + delta)
        self.refresh()

    def action_jump(self, index: int) -> None:
        self.cursor = self._clamp(index if index >= 0 else len(self.tokens) - 1)
        self.anchor = None
        self.refresh()

    def action_toggle(self, field: Field) -> None:
        picked = list(self.selection())
        if not picked:
            return
        # All on → turn it off; otherwise turn it on everywhere selected.
        on = all(field in self.labels[i] for i in picked)
        one: Labels = frozenset({field})
        for i in picked:
            self.labels[i] = self.labels[i] - one if on else self.labels[i] | one
        self.refresh()
        self.post_message(self.Changed())

    def action_skip(self) -> None:
        for i in self.selection():
            self.labels[i] = SKIP
        self.refresh()
        self.post_message(self.Changed())

    def render(self) -> Text:
        if not self.tokens:
            return Text("Paste the page's title above; its words appear here.", style="dim")
        text = Text()
        chosen = set(self.selection()) if self.has_focus else set()
        for i, (token, label) in enumerate(zip(self.tokens, self.labels, strict=True)):
            style = _STYLES.get(frozenset(label), "white")
            if i in chosen:
                style += " reverse"
            text.append(token.text, style=style)
            text.append(" ")
        return text


# ── Engine messages ────────────────────────────────────────────────────


class JobStarted(Message):
    def __init__(self, job: engine.Job) -> None:
        super().__init__()
        self.job = job


class JobProgress(Message):
    def __init__(self, job: engine.Job, progress: Progress) -> None:
        super().__init__()
        self.job, self.progress = job, progress


class JobDone(Message):
    def __init__(self, job: engine.Job) -> None:
        super().__init__()
        self.job = job


# ── The queue table ────────────────────────────────────────────────────


class QueueTable(DataTable[str]):
    BINDINGS = [
        Binding("r", "app.retry", "Retry"),
        Binding("d", "app.remove", "Remove / cancel"),
        Binding("D", "app.clear_done", "Clear finished"),
    ]


_STATUS_TEXT = {
    "queued": "[dim]queued[/]",
    "downloading": "[yellow]downloading[/]",
    "done": "[green]✓ done[/]",
    "failed": "[red]✗ failed[/]",
    "cancelled": "[dim]cancelled[/]",
}


# ── The app ────────────────────────────────────────────────────────────


class DownloadApp(App[None]):
    TITLE = "video-download"
    CSS = """
    Screen { layout: vertical; }
    #form { height: auto; padding: 0 1; border: round $primary; }
    #form Input { margin: 0 0 0 0; }
    .row { height: auto; }
    .row Label { width: 12; padding: 1 1 0 0; text-align: right; }
    .row Input { width: 1fr; }
    #words-box { height: auto; padding: 0 0 0 12; }
    #legend { color: $text-muted; padding: 0 0 0 13; }
    #preview { padding: 1 0 0 13; }
    QueueTable { height: 1fr; }
    """
    BINDINGS = [
        Binding("ctrl+s", "add", "Add to queue", priority=True),
        Binding("ctrl+q", "quit_app", "Quit", priority=True),
    ]

    def __init__(self, folder: Path, workers: int = 2, known: KnownNames | None = None) -> None:
        super().__init__()
        self.folder = folder
        self.parallel = workers   # not `workers`: App.workers is Textual's worker manager
        self.library_known = known or KnownNames()
        self.items: list[QueueItem] = []
        self.jobs: dict[str, engine.Job] = {}       # row key -> its running/queued job
        self.rows: dict[str, QueueItem] = {}
        self._next_key = 0
        self._quit_armed = False
        self.engine = engine.Engine(workers, on_start=self._on_start, on_line=self._on_line,
                                    on_done=self._on_done)

    # Engine callbacks: on worker threads, so they only post messages (thread-safe).

    def _on_start(self, job: engine.Job) -> None:
        self.post_message(JobStarted(job))

    def _on_line(self, job: engine.Job, _line: str, prog: Progress | None) -> None:
        if prog is not None:
            self.post_message(JobProgress(job, prog))

    def _on_done(self, job: engine.Job) -> None:
        self.post_message(JobDone(job))

    # ── layout ──

    def compose(self) -> ComposeResult:
        with Vertical(id="form"):
            with Horizontal(classes="row"):
                yield Label("URL")
                yield Input(placeholder="https://…", id="url")
            with Horizontal(classes="row"):
                yield Label("Page title")
                yield Input(placeholder="paste the page's title", id="title")
            with Vertical(id="words-box"):
                yield WordBar(id="words")
            yield Static("←/→ move · shift+←/→ select · [b magenta]p[/] people · "
                         "[b]t[/] title · [b cyan]c[/] collection · x skip", id="legend")
            with Horizontal(classes="row"):
                yield Label("People")
                yield Input(placeholder="Alice, Bob", id="people")
            with Horizontal(classes="row"):
                yield Label("Title")
                yield Input(id="name-title")
            with Horizontal(classes="row"):
                yield Label("Collection")
                yield Input(id="collection")
            yield Static("", id="preview")
        yield QueueTable(id="queue", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(QueueTable)
        table.add_column("Name", key="name")
        table.add_column("Status", key="status", width=13)
        table.add_column("Progress", key="progress", width=9)
        table.add_column("Speed", key="speed", width=12)
        table.add_column("ETA", key="eta", width=8)
        table.add_column("Error", key="error")
        for item in load_queue_file(self.folder):
            self._add_row(item)
            if item.status == "queued":
                self._start(item)
        self._update_preview()
        self.query_one("#url", Input).focus()

    # ── known names ──

    def known(self) -> KnownNames:
        history = known_from_queue(self.items)
        return KnownNames(self.library_known.people | history.people,
                          self.library_known.collections | history.collections)

    # ── form ──

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "title":
            tokens = tokenize(event.value)
            self.query_one(WordBar).set_tokens(tokens, auto_label(tokens, self.known()))
        elif event.input.id in ("people", "name-title", "collection"):
            self._update_preview()

    def on_word_bar_changed(self, _event: WordBar.Changed) -> None:
        bar = self.query_one(WordBar)
        parts = compose(bar.tokens, bar.labels)
        self.query_one("#people", Input).value = ", ".join(parts.people)
        self.query_one("#name-title", Input).value = parts.title
        self.query_one("#collection", Input).value = parts.collection
        self._update_preview()

    def _parts(self):  # noqa: ANN202
        return parts_from_fields(self.query_one("#people", Input).value,
                                 self.query_one("#name-title", Input).value,
                                 self.query_one("#collection", Input).value)

    def _update_preview(self) -> None:
        parts = self._parts()
        preview = self.query_one("#preview", Static)
        if not parts.stem:
            preview.update("[dim]→ named after the video's own title (nothing labelled)[/]")
            return
        lines = [f"→ [b]{_escape(parts.stem)}[/b][dim].ext[/]"]
        lines += [f"[yellow]⚠ {_escape(p)}[/]" for p in parts.problems()]
        preview.update("\n".join(lines))

    def action_add(self) -> None:
        url = self.query_one("#url", Input).value.strip()
        if not url:
            self.notify("Paste a URL first", severity="warning")
            return
        if any(i.url == url and i.status in ("queued", "downloading") for i in self.items):
            self.notify("That URL is already in the queue", severity="warning")
            return
        item = QueueItem(url=url, name=self._parts().stem,
                         added=datetime.now().isoformat(timespec="seconds"))
        self._add_row(item)
        self._save()
        self._start(item)
        for widget_id in ("url", "title", "people", "name-title", "collection"):
            self.query_one(f"#{widget_id}", Input).value = ""
        self.query_one(WordBar).set_tokens([], [])
        self.query_one("#url", Input).focus()
        self.notify(f"Queued {item.name or url}")

    # ── queue ──

    def _add_row(self, item: QueueItem) -> str:
        key = str(self._next_key)
        self._next_key += 1
        self.items.append(item)
        self.rows[key] = item
        self.query_one(QueueTable).add_row(
            _escape(item.name or item.url), _STATUS_TEXT[item.status], "", "", "",
            f"[red]{_escape(item.error)}[/]", key=key)
        return key

    def _key_of(self, item: QueueItem) -> str:
        return next(k for k, v in self.rows.items() if v is item)

    def _start(self, item: QueueItem) -> None:
        key = self._key_of(item)
        item.status, item.error = "queued", ""
        job = engine.Job(id=key, url=item.url, name=item.name or None)
        self.jobs[key] = job
        self._set_status(key, item)
        self.engine.submit(job)

    def _set_status(self, key: str, item: QueueItem) -> None:
        table = self.query_one(QueueTable)
        table.update_cell(key, "status", _STATUS_TEXT[item.status])
        if item.status == "done":
            table.update_cell(key, "progress", "100%")
        if item.status != "downloading":
            table.update_cell(key, "speed", "")
            table.update_cell(key, "eta", "")
        table.update_cell(key, "error", f"[red]{_escape(item.error)}[/]")

    def _save(self) -> None:
        save_queue_file(self.folder, self.items)

    def on_job_started(self, message: JobStarted) -> None:
        key = str(message.job.id)
        if key in self.rows:
            self.rows[key].status = "downloading"
            self._set_status(key, self.rows[key])
            self._save()

    def on_job_progress(self, message: JobProgress) -> None:
        key, prog = str(message.job.id), message.progress
        if key in self.rows:
            table = self.query_one(QueueTable)
            table.update_cell(key, "progress", f"{prog.percent:5.1f}%")
            table.update_cell(key, "speed", prog.speed)
            table.update_cell(key, "eta", prog.eta)

    def on_job_done(self, message: JobDone) -> None:
        job, key = message.job, str(message.job.id)
        item = self.rows.get(key)
        if item is None or self.jobs.get(key) is not job:
            return   # removed, or retried since
        if job.cancelled:
            item.status = "cancelled"
        elif job.returncode == 0:
            item.status = "done"
        else:
            item.status = "failed"
            item.error = job.tail[-1] if job.tail else f"yt-dlp exited {job.returncode}"
            self.notify(f"Failed: {item.name or item.url}\n{item.error}", severity="error",
                        timeout=10)
        self.jobs.pop(key, None)
        self._set_status(key, item)
        self._save()

    def _selected(self) -> tuple[str, QueueItem] | None:
        table = self.query_one(QueueTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return (key, self.rows[key]) if key in self.rows else None

    def action_retry(self) -> None:
        picked = self._selected()
        if picked and picked[1].status in ("failed", "cancelled"):
            self._start(picked[1])
            self._save()

    def action_remove(self) -> None:
        picked = self._selected()
        if picked is None:
            return
        key, item = picked
        if key in self.jobs:          # queued or running: cancel; the row stays as cancelled
            self.engine.cancel(self.jobs[key])
            if item.status == "queued":
                item.status = "cancelled"
                self.jobs.pop(key)
                self._set_status(key, item)
                self._save()
            return
        self.query_one(QueueTable).remove_row(key)
        self.items.remove(item)
        del self.rows[key]
        self._save()

    def action_clear_done(self) -> None:
        for key, item in list(self.rows.items()):
            if item.status == "done":
                self.query_one(QueueTable).remove_row(key)
                self.items.remove(item)
                del self.rows[key]
        self._save()

    # ── quitting ──

    def action_quit_app(self) -> None:
        running = [i for i in self.items if i.status == "downloading"]
        if running and not self._quit_armed:
            self._quit_armed = True
            self.notify(f"{len(running)} download(s) running — press ctrl+q again to quit. "
                        "They'll resume next time.", severity="warning")
            return
        self.exit()

    def on_unmount(self) -> None:
        # Running and queued downloads are saved as queued (the queue file treats an
        # interrupted download that way), and their yt-dlp processes are stopped.
        self._save()
        self.engine.shutdown(cancel=True, wait=False)


def _escape(text: str) -> str:
    from rich.markup import escape
    return escape(text)
